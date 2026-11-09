"""Shared reviewed-write mechanics: one proposal, one atomic outcome, one receipt.

What a proposal contains and does comes from app.services.ai_action_kinds.
"""
from datetime import datetime, timedelta
import hashlib
import json
import re
from uuid import UUID, uuid4

from app.models import AIClient, AIConnection, AIToolAudit, AIWriteAction
from app.services.ai_action_kinds import ActionConflict, CREATE_LEAD, kind_of, validate_lead  # noqa: F401
from app.services.ai_connections import AIConnectionService, resource_uri
from app.services.base import TenantService
from app.services.errors import RecordNotFound

REQUIRED_SCOPES = list(CREATE_LEAD.scopes)
ACTION_SECONDS = 600
MAX_DAILY_PROPOSALS = 100


def payload_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=True).encode()).hexdigest()


class AIActionService(TenantService):
    def grant(self, connection_id, scopes=REQUIRED_SCOPES):
        # Global order: grant, then action. Revocation and OAuth exchanges use it too.
        grant = self._query(AIConnection).populate_existing().filter_by(
            id=connection_id, user_id=self.principal.user_id).with_for_update().first()
        if grant is None:
            raise RecordNotFound('Action unavailable')
        AIConnectionService(self.session, self.principal).require_permissions(grant.id,
            client_id=grant.client_id, resource=resource_uri(), scopes=list(scopes))
        client = self.session.query(AIClient).filter_by(id=grant.client_id,
            is_active=True, oauth_enabled=True).first()
        if client is None:
            raise PermissionError('Connection unavailable')
        return grant

    def action(self, ident, *, connection_id=None):
        try:
            if not isinstance(ident, str) or str(UUID(ident)) != ident:
                raise ValueError()
        except (ValueError, AttributeError):
            raise RecordNotFound('Action unavailable') from None
        query = self._query(AIWriteAction).filter_by(id=ident, user_id=self.principal.user_id)
        if connection_id is not None:
            query = query.filter_by(connection_id=connection_id)
        row = query.first()
        if row is None:
            raise RecordNotFound('Action unavailable')
        self.grant(row.connection_id, kind_of(row.kind).scopes)
        return query.populate_existing().with_for_update().one()

    def prepare(self, connection_id, key, value, kind=CREATE_LEAD.name):
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,64}', key):
            raise ValueError('Supply a unique request key of 16 to 64 letters, numbers, underscores or hyphens')
        action = kind_of(kind)
        if action is CREATE_LEAD:
            # Fingerprint the caller's validated input; kept for existing request keys.
            fingerprint = payload_hash(validate_lead(value).model_dump(mode='json'))
        else:
            fingerprint = payload_hash({'kind': kind, 'input': value})
        self.grant(connection_id, action.scopes)
        row = self._query(AIWriteAction).filter_by(connection_id=connection_id,
            user_id=self.principal.user_id, request_key=key).first()
        if row is not None:
            if row.input_hash != fingerprint or row.kind != kind:
                raise ActionConflict('This request key belongs to a different proposal')
            return self.result(row)
        now = datetime.utcnow()
        count = self._query(AIWriteAction).filter_by(user_id=self.principal.user_id, connection_id=connection_id).filter(
            AIWriteAction.created_at > now-timedelta(days=1)).count()
        if count >= MAX_DAILY_PROPOSALS:
            raise ActionConflict('Daily proposal limit reached')
        fields = action.prepare(self.session, self.principal, value)
        row = AIWriteAction(id=str(uuid4()), tenant_id=self.principal.tenant_id,
            user_id=self.principal.user_id, connection_id=connection_id, request_key=key,
            input_hash=fingerprint, kind=kind, payload=fields, status='pending',
            created_at=now, expires_at=now+timedelta(seconds=ACTION_SECONDS))
        self.session.add(row)
        self.session.flush()
        return self.result(row)

    def result(self, row):
        state = row.status
        if state == 'pending' and row.expires_at <= datetime.utcnow():
            state = 'expired'
        result = {'action_id': row.id, 'status': state,
                  'expires_at': row.expires_at.isoformat()+'Z'}
        if state == 'pending':
            result['review_url'] = resource_uri().removesuffix('/mcp')+'/oauth/actions/'+row.id
        if state == 'committed':
            action = kind_of(row.kind)
            result[action.receipt_key] = action.record(self.session, self.principal, row.result_id)
        return result

    def preview(self, ident):
        row = self.action(ident)
        result = self.result(row)
        action = kind_of(row.kind)
        pending = row.status == 'pending' and row.expires_at > datetime.utcnow()
        if pending:
            result[action.receipt_key] = row.payload
        result['presentation'] = action.presentation(row.payload if pending else None)
        return row, result

    def decide(self, ident, approved, expected_hash):
        if type(approved) is not bool:
            raise ValueError('Explicit decision required')
        row = self.action(ident)
        if expected_hash != payload_hash(row.payload):
            raise ActionConflict('Proposal changed; review it again')
        if row.status != 'pending':
            return self.result(row)
        if row.expires_at <= datetime.utcnow():
            raise ActionConflict('Proposal expired; return to your AI app to prepare a new one')
        now = datetime.utcnow()
        action = kind_of(row.kind)
        if approved:
            row.result_id = action.execute(self.session, self.principal, row.payload)
            row.status = 'committed'
        else:
            row.status = 'cancelled'
        row.decided_at = now
        self.session.add(AIToolAudit(id=str(uuid4()), tenant_id=self.principal.tenant_id,
            user_id=self.principal.user_id, connection_id=row.connection_id,
            tool=action.confirm_audit if approved else action.cancel_audit,
            outcome='success', result_count=1 if approved else 0, created_at=now))
        self.session.flush()
        return self.result(row)
