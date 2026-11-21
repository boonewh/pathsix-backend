"""Retry-safe creates and updates for mobile clients (mobile Phase 0, item 5).

A client sends `Idempotency-Key: <16-64 letters, digits, _ or ->` with a create
or update. The first successful outcome is stored as a write receipt in the
same transaction as the write, so both commit or neither does. A retry with the
same key and the same request is acknowledged without writing again; the same
key with a different request is refused. This is the request-key rule of the AI
connector's ai_write_actions (app/services/ai_actions.py), reusing its key
format and payload_hash, scoped to the signed-in user instead of a connection.

A replay is an acknowledgement, not a read: it returns the original status and
only `{"id": <record id>}`. The record's content is never stored in the receipt
or replayed, so a replay cannot disclose data the caller has since lost access
to. Clients read the current state through the record routes or the change feed.

Concurrent retries of one key are serialized with a transaction-scoped advisory
lock taken before the receipt is read. This relies on READ COMMITTED (the
application's isolation level), so the waiter's next statement sees the winner's
committed receipt; the unique constraint is the backstop. The lock wait is
bounded; on timeout the client retries later with the same key.

Replay and conflict apply to requests that pass the route's current validation:
routes validate the body before the key is checked. Without the header nothing
changes. Receipts are kept 90 days (scripts/prune_tombstones.py); a key reused
after that writes again (docs/mobile-writes.md sets the client retry deadline).
"""
import re

from quart import jsonify, request
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.models import WriteReceipt
from app.services.ai_actions import payload_hash
from app.services.database_context import bind_principal
from app.services.principal import Principal

HEADER = 'Idempotency-Key'
KEY = re.compile(r'[A-Za-z0-9_-]{16,64}')
LOCK_WAIT = '5s'
_PENDING = 'idempotency_pending'


class IdempotencyKeyInvalid(ValueError):
    code = 'invalid_idempotency_key'


class IdempotencyConflict(Exception):
    code = 'idempotency_conflict'


class IdempotencyInProgress(Exception):
    code = 'idempotency_in_progress'


class IdempotencyKeyRequired(ValueError):
    code = 'idempotency_key_required'


# What a receipt stores and a replay returns: identifiers only, never content.
# The existing routes keep `id` alone; the mobile record routes add the record's
# canonical `uuid` (app/services/mobile_records.py).
ID_ONLY = ('id',)
ID_AND_UUID = ('id', 'uuid')


def _acknowledgement(body, status, *, replayed):
    response = jsonify(body)
    response.status_code = status
    response.headers['Cache-Control'] = 'no-store'
    if replayed:
        response.headers['Idempotent-Replayed'] = 'true'
    return response


def _record_id(result):
    if isinstance(result, dict) and type(result.get('id')) is int:
        return result['id']
    # An update whose response carries no id (contacts): the route's target.
    targets = [v for v in (request.view_args or {}).values() if type(v) is int]
    if len(targets) != 1:
        raise RuntimeError('Cannot identify the written record for its receipt')
    return targets[0]


class IdempotencyService:
    def __init__(self, session, principal: Principal):
        if not isinstance(principal, Principal):
            raise TypeError('An authenticated principal is required')
        self.session = session
        self.principal = principal
        bind_principal(session, principal)

    def _lock(self, key):
        p = self.principal
        previous = self.session.execute(text("SELECT current_setting('lock_timeout')")).scalar_one()
        self.session.execute(text("SELECT set_config('lock_timeout', :wait, true)"), {'wait': LOCK_WAIT})
        try:
            # Held until this transaction ends: a concurrent retry waits here, then
            # finds the committed receipt (or, if this attempt failed, none).
            self.session.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))'),
                                 {'k': f'write-receipt:{p.tenant_id}:{p.user_id}:{key}'})
        except OperationalError as exc:
            if getattr(exc.orig, 'pgcode', None) == '55P03':  # lock_not_available
                raise IdempotencyInProgress('A request with this Idempotency-Key is still running; '
                                            'retry shortly with the same key') from None
            raise
        # The bound applies to waiting for the key only, not to the write itself.
        self.session.execute(text("SELECT set_config('lock_timeout', :wait, true)"), {'wait': previous})

    def begin(self, body, *, required=False, identifiers=ID_ONLY):
        """Call first in the write's session. Returns a replay response or None."""
        key = request.headers.get(HEADER)
        if key is None:
            if required:
                raise IdempotencyKeyRequired('This request needs an Idempotency-Key header')
            return None
        if not KEY.fullmatch(key):
            raise IdempotencyKeyInvalid(
                'Idempotency-Key must be 16 to 64 letters, numbers, underscores or hyphens')
        p = self.principal
        fingerprint = payload_hash({'endpoint': request.endpoint, 'target': request.view_args or {},
                                    'input': body})
        if self.session.get_bind().dialect.name == 'postgresql':
            self._lock(key)
        receipt = self.session.query(WriteReceipt).filter_by(
            tenant_id=p.tenant_id, user_id=p.user_id, request_key=key).first()
        if receipt is not None:
            if receipt.input_hash != fingerprint:
                raise IdempotencyConflict('This Idempotency-Key was used for a different request')
            stored = receipt.response or {}
            return _acknowledgement({k: stored.get(k) for k in identifiers if k in stored},
                                    receipt.status_code, replayed=True)
        self.session.info[_PENDING] = (key, fingerprint)
        return None

    def finish(self, result, status, *, identifiers=ID_ONLY):
        """Record the outcome in the same transaction, just before the route commits."""
        pending = self.session.info.pop(_PENDING, None)
        if pending is None:
            return False
        key, fingerprint = pending
        stored = {'id': _record_id(result)}
        if 'uuid' in identifiers:
            stored['uuid'] = str(result['uuid'])
        self.session.add(WriteReceipt(
            tenant_id=self.principal.tenant_id, user_id=self.principal.user_id, request_key=key,
            endpoint=request.endpoint[:100], input_hash=fingerprint, status_code=status,
            response=stored))
        self.session.flush()
        return True


def idempotent_write(session, principal, body, perform, status=200, *, no_store=False):
    """Run one create/update route's write with optional Idempotency-Key handling.

    `perform()` does the write and returns the route's normal JSON result; the
    session is committed here, together with the receipt when a key was sent.
    The first response is the route's normal one; replays are acknowledgements.
    """
    service = IdempotencyService(session, principal)
    replay = service.begin(body)
    if replay is not None:
        return replay
    result = perform()
    service.finish(result, status)
    session.commit()
    response = jsonify(result)
    response.status_code = status
    if no_store:
        response.headers['Cache-Control'] = 'no-store'
    return response


def mobile_write(session, principal, body, perform, status=200):
    """A mobile record route's write: the key is required, and the receipt keeps
    the record's canonical uuid as well as its id.

    `perform()` returns `{id, uuid, record}`. The first response is that; a
    replay returns `{id, uuid}` only. Anything `perform()` raises leaves no
    receipt (the caller rolls back).
    """
    service = IdempotencyService(session, principal)
    replay = service.begin(body, required=True, identifiers=ID_AND_UUID)
    if replay is not None:
        return replay
    result = perform()
    service.finish(result, status, identifiers=ID_AND_UUID)
    session.commit()
    response = jsonify(result)
    response.status_code = status
    response.headers['Cache-Control'] = 'no-store'
    return response


def register_idempotency_errors(app):
    @app.errorhandler(IdempotencyConflict)
    async def _conflict(exc):
        return jsonify({'code': exc.code, 'error': str(exc)}), 409

    @app.errorhandler(IdempotencyInProgress)
    async def _in_progress(exc):
        response = jsonify({'code': exc.code, 'error': str(exc)})
        response.status_code = 409
        response.headers['Retry-After'] = '1'
        return response
