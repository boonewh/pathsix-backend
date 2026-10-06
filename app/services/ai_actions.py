"""One reviewed lead proposal, one atomic creation, one durable outcome."""
from datetime import datetime, timedelta
import hashlib
import json
import re
from uuid import UUID, uuid4

from app.models import AIClient, AIConnection, AIToolAudit, AIWriteAction
from app.schemas.leads import LeadCreateSchema
from app.services.ai_connections import AIConnectionService, resource_uri
from app.services.base import TenantService
from app.services.errors import RecordNotFound
from app.services.leads import LeadService

REQUIRED_SCOPES = ['leads:read', 'leads:create']
ACTION_SECONDS = 600
MAX_DAILY_PROPOSALS = 100


class ActionConflict(ValueError):
    pass


def payload_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=True).encode()).hexdigest()


def validate_lead(value):
    if not isinstance(value, dict) or set(value) - set(LeadCreateSchema.model_fields):
        raise ValueError('Supply only supported lead fields')
    if any(v is not None and not isinstance(v, str) for v in value.values()):
        raise ValueError('Lead fields must be text')
    if any(isinstance(v, str) and len(v) > 4000 for v in value.values()):
        raise ValueError('Lead fields are too long')
    return LeadCreateSchema.model_validate(value)


class AIActionService(TenantService):
    def grant(self, connection_id):
        # Global order: grant, then action. Revocation and OAuth exchanges use it too.
        grant = self._query(AIConnection).populate_existing().filter_by(
            id=connection_id, user_id=self.principal.user_id).with_for_update().first()
        if grant is None:
            raise RecordNotFound('Action unavailable')
        AIConnectionService(self.session, self.principal).require_permissions(grant.id,
            client_id=grant.client_id, resource=resource_uri(), scopes=REQUIRED_SCOPES)
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
        self.grant(row.connection_id)
        return query.populate_existing().with_for_update().one()

    def prepare(self, connection_id, key, value):
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,64}', key):
            raise ValueError('Supply a unique request key of 16 to 64 letters, numbers, underscores or hyphens')
        validated = validate_lead(value)
        original = validated.model_dump(mode='json')
        fingerprint = payload_hash(original)
        self.grant(connection_id)
        row = self._query(AIWriteAction).filter_by(connection_id=connection_id,
            user_id=self.principal.user_id, request_key=key).first()
        if row is not None:
            if row.input_hash != fingerprint:
                raise ActionConflict('This request key belongs to a different proposal')
            return self.result(row)
        now = datetime.utcnow()
        count = self._query(AIWriteAction).filter_by(user_id=self.principal.user_id, connection_id=connection_id).filter(
            AIWriteAction.created_at > now-timedelta(days=1)).count()
        if count >= MAX_DAILY_PROPOSALS:
            raise ActionConflict('Daily proposal limit reached')
        # The shared service prepares exactly the values it will persist, including defaults.
        fields = LeadService(self.session, self.principal).creation_fields(validated)
        row = AIWriteAction(id=str(uuid4()), tenant_id=self.principal.tenant_id,
            user_id=self.principal.user_id, connection_id=connection_id, request_key=key,
            input_hash=fingerprint, kind='create_lead', payload=fields, status='pending',
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
            # Receipts do not preserve permission to read a subsequently hidden/deleted lead.
            lead = LeadService(self.session, self.principal)._get(row.result_id)
            result['lead'] = {'id': lead.id, 'name': lead.name}
        return result

    def preview(self, ident):
        row = self.action(ident)
        result = self.result(row)
        if row.status == 'pending' and row.expires_at > datetime.utcnow():
            result['lead'] = row.payload
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
            raise ActionConflict('Proposal expired; return to ChatGPT to prepare a new one')
        now = datetime.utcnow()
        if approved:
            service = LeadService(self.session, self.principal)
            data = validate_lead(row.payload)
            if service.creation_fields(data) != row.payload:
                raise ActionConflict('Lead defaults changed; prepare and review a new proposal')
            row.result_id = service.create(data)
            row.status = 'committed'
        else:
            row.status = 'cancelled'
        row.decided_at = now
        self.session.add(AIToolAudit(id=str(uuid4()), tenant_id=self.principal.tenant_id,
            user_id=self.principal.user_id, connection_id=row.connection_id,
            tool='confirm_lead_creation' if approved else 'cancel_lead_creation',
            outcome='success', result_count=1 if approved else 0, created_at=now))
        self.session.flush()
        return self.result(row)
