"""Retry-safe creates and updates for mobile clients (mobile Phase 0, item 5).

A client sends `Idempotency-Key: <16-64 letters, digits, _ or ->` with a create
or update. The first successful outcome is stored as a write receipt in the
same transaction as the write, so both commit or neither does. A retry with the
same key and the same request replays the stored status and body; the same key
with a different request is refused. This is the request-key rule of the AI
connector's ai_write_actions (app/services/ai_actions.py), reusing its key
format and payload_hash, scoped to the signed-in user instead of a connection.

Concurrent retries of one key are serialized with a transaction-scoped advisory
lock taken before the receipt is read; the unique constraint is the backstop.
Without the header nothing changes. Receipts are kept 90 days
(scripts/prune_tombstones.py); a key reused after that writes again.
"""
import json
import re

from quart import current_app, jsonify, request
from sqlalchemy import text

from app.models import WriteReceipt
from app.services.ai_actions import payload_hash
from app.services.database_context import bind_principal
from app.services.principal import Principal

HEADER = 'Idempotency-Key'
KEY = re.compile(r'[A-Za-z0-9_-]{16,64}')
_PENDING = 'idempotency_pending'


class IdempotencyKeyInvalid(ValueError):
    code = 'invalid_idempotency_key'


class IdempotencyConflict(Exception):
    code = 'idempotency_conflict'


def _replay(receipt):
    response = jsonify(receipt.response)
    response.status_code = receipt.status_code
    response.headers['Idempotent-Replayed'] = 'true'
    response.headers['Cache-Control'] = 'no-store'
    return response


class IdempotencyService:
    def __init__(self, session, principal: Principal):
        if not isinstance(principal, Principal):
            raise TypeError('An authenticated principal is required')
        self.session = session
        self.principal = principal
        bind_principal(session, principal)

    def begin(self, body):
        """Call first in the write's session. Returns a replay response or None."""
        key = request.headers.get(HEADER)
        if key is None:
            return None
        if not KEY.fullmatch(key):
            raise IdempotencyKeyInvalid(
                'Idempotency-Key must be 16 to 64 letters, numbers, underscores or hyphens')
        p = self.principal
        fingerprint = payload_hash({'endpoint': request.endpoint, 'target': request.view_args or {},
                                    'input': body})
        if self.session.get_bind().dialect.name == 'postgresql':
            # Held until this transaction ends: a concurrent retry waits here, then
            # finds the committed receipt (or, if this attempt failed, none).
            self.session.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))'),
                                 {'k': f'write-receipt:{p.tenant_id}:{p.user_id}:{key}'})
        receipt = self.session.query(WriteReceipt).filter_by(
            tenant_id=p.tenant_id, user_id=p.user_id, request_key=key).first()
        if receipt is not None:
            if receipt.input_hash != fingerprint:
                raise IdempotencyConflict('This Idempotency-Key was used for a different request')
            return _replay(receipt)
        self.session.info[_PENDING] = (key, fingerprint)
        return None

    def finish(self, body, status):
        """Record the outcome in the same transaction, just before the route commits."""
        pending = self.session.info.pop(_PENDING, None)
        if pending is None:
            return
        key, fingerprint = pending
        self.session.add(WriteReceipt(
            tenant_id=self.principal.tenant_id, user_id=self.principal.user_id, request_key=key,
            endpoint=request.endpoint[:100], input_hash=fingerprint, status_code=status, response=body))
        self.session.flush()


def idempotent_write(session, principal, body, perform, status=200, *, no_store=False):
    """Run one create/update route's write with optional Idempotency-Key handling.

    `perform()` does the write and returns the JSON-able result; the session is
    committed here, together with the receipt when a key was sent.
    """
    service = IdempotencyService(session, principal)
    replay = service.begin(body)
    if replay is not None:
        return replay
    result = perform()
    # Store exactly what the client receives, in JSON-safe form.
    stored = json.loads(current_app.json.dumps(result))
    service.finish(stored, status)
    session.commit()
    response = jsonify(stored)
    response.status_code = status
    if no_store:
        response.headers['Cache-Control'] = 'no-store'
    return response


def register_idempotency_errors(app):
    @app.errorhandler(IdempotencyConflict)
    async def _conflict(exc):
        return jsonify({'code': exc.code, 'error': str(exc)}), 409
