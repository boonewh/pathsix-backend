"""Owner-only consent lifecycle. This module never issues or authenticates tokens."""
from datetime import datetime, timedelta
from uuid import UUID, uuid4
from urllib.parse import urlsplit
import os

from sqlalchemy.orm import selectinload
from app.models import AIClient, AIConnection, User, Tenant
from app.services.base import TenantService
from app.services.errors import RecordNotFound

READ_SCOPES = {
    'clients:read': 'Read clients you can access',
    'leads:read': 'Read leads you can access',
    'projects:read': 'Read projects you can access',
    'interactions:read': 'Read interactions you can access',
    'reports:read': 'Read your company reports (administrators only)',
}
SCOPES = {**READ_SCOPES, 'leads:create': 'Create leads after you review and confirm each proposal'}
MAX_ACTIVE_CONNECTIONS = 20
MAX_DAILY_CONSENTS = 100
CONSENT_DAYS = 30


class ConnectionLimit(ValueError):
    pass


class AIConfigurationError(RuntimeError):
    pass


def resource_uri():
    value = os.getenv('MCP_RESOURCE_URI', '')
    try:
        parsed = urlsplit(value)
        parsed.port  # Reject malformed ports in the configured audience.
        valid = (0 < len(value) <= 500 and parsed.scheme == 'https' and parsed.hostname
                 and not parsed.username and not parsed.password and not parsed.query
                 and not parsed.fragment and not any(c.isspace() for c in value))
    except ValueError:
        valid = False
    if not valid:
        raise AIConfigurationError('AI connections are not configured')
    return value


def scope_set(value):
    if (not isinstance(value, list) or not 1 <= len(value) <= len(SCOPES)
            or any(not isinstance(s, str) or s not in SCOPES for s in value)
            or len(set(value)) != len(value)):
        raise ValueError('Choose one or more supported permissions')
    return frozenset(value)


class AIConnectionService(TenantService):
    def _user(self, *, lock=False):
        query = self._query(User).join(Tenant, Tenant.id == User.tenant_id).filter(
            User.id == self.principal.user_id, User.is_active.is_(True),
            Tenant.id == self.principal.tenant_id, Tenant.is_active.is_(True))
        query = query.populate_existing().options(selectinload(User.roles))
        if lock:
            query = query.with_for_update(of=User)
        user = query.first()
        if user is None:
            raise PermissionError('Active user and tenant required')
        return user

    @staticmethod
    def _permitted(user):
        scopes = set(SCOPES)
        if not any(role.name == 'admin' for role in user.roles):
            scopes.remove('reports:read')
        return scopes

    def _client(self, client_id):
        if not isinstance(client_id, str) or not 1 <= len(client_id) <= 200:
            raise ValueError('Invalid AI client')
        client = self.session.query(AIClient).populate_existing().filter(
            AIClient.id == client_id, AIClient.is_active.is_(True)).first()
        if client is None:
            raise RecordNotFound('AI client unavailable')
        # A malformed operator registration fails closed too.
        scope_set(client.allowed_scopes)
        return client

    def _owned(self):
        return self._query(AIConnection).filter(AIConnection.user_id == self.principal.user_id)

    def _connection(self, ident, *, lock=False):
        try:
            if not isinstance(ident, str) or str(UUID(ident)) != ident:
                raise ValueError()
        except (ValueError, AttributeError):
            raise RecordNotFound('AI connection not found') from None
        query = self._owned().populate_existing().filter(AIConnection.id == ident)
        if lock:
            query = query.with_for_update()
        row = query.first()
        if row is None:
            raise RecordNotFound('AI connection not found')
        return row

    @staticmethod
    def _serialize(row):
        now = datetime.utcnow()
        return {'id': row.id, 'client_id': row.client_id, 'resource': row.resource,
                'scopes': list(row.scopes),
                'status': 'revoked' if row.revoked_at else 'expired' if row.expires_at <= now else 'active',
                'created_at': row.created_at.isoformat() + 'Z',
                'expires_at': row.expires_at.isoformat() + 'Z',
                'revoked_at': row.revoked_at.isoformat() + 'Z' if row.revoked_at else None,
                'last_used_at': row.last_used_at.isoformat() + 'Z' if row.last_used_at else None}

    def catalog(self):
        user = self._user()
        permitted = self._permitted(user)
        clients = self.session.query(AIClient).filter(AIClient.is_active.is_(True)).order_by(AIClient.id).limit(100).all()
        result = []
        for client in clients:
            available = sorted(scope_set(client.allowed_scopes) & permitted)
            if available:
                result.append({'id': client.id, 'name': client.name, 'scopes': available, 'oauth_enabled': client.oauth_enabled})
        return {'clients': result, 'scopes': {s: SCOPES[s] for s in sorted(permitted)},
                'consent_days': CONSENT_DAYS, 'tokens_available': any(client['oauth_enabled'] for client in result)}

    def preview(self, data):
        if not isinstance(data, dict) or set(data) != {'client_id', 'scopes'}:
            raise ValueError('Supply an AI client and requested permissions')
        user = self._user()
        client = self._client(data['client_id'])
        scopes = scope_set(data['scopes'])
        if not scopes <= (scope_set(client.allowed_scopes) & self._permitted(user)):
            raise PermissionError('Requested permissions are not available')
        return {'client_id': client.id, 'client_name': client.name, 'resource': resource_uri(),
                'scopes': sorted(scopes), 'permissions': [SCOPES[s] for s in sorted(scopes)],
                'consent_days': CONSENT_DAYS}

    def consent(self, data):
        if (not isinstance(data, dict) or set(data) != {'client_id', 'scopes', 'approved'}
                or data['approved'] is not True):
            raise ValueError('Explicit approval of the selected permissions is required')
        # Serialize consent creation per owner, including across app machines.
        self._user(lock=True)
        preview = self.preview({k: data[k] for k in ('client_id', 'scopes')})
        now = datetime.utcnow()
        active = self._owned().filter(AIConnection.revoked_at.is_(None), AIConnection.expires_at > now).count()
        if active >= MAX_ACTIVE_CONNECTIONS:
            raise ConnectionLimit('Revoke an existing AI connection before adding another')
        recent = self._owned().filter(AIConnection.created_at >= now-timedelta(days=1)).count()
        if recent >= MAX_DAILY_CONSENTS:
            raise ConnectionLimit('Daily AI connection approval limit reached. Try again later.')
        row = AIConnection(id=str(uuid4()), tenant_id=self.principal.tenant_id,
                           user_id=self.principal.user_id, client_id=preview['client_id'],
                           resource=preview['resource'], scopes=preview['scopes'],
                           created_at=now, expires_at=now + timedelta(days=CONSENT_DAYS))
        self.session.add(row)
        self.session.flush()
        return self._serialize(row)

    def list(self, page=1, per_page=20):
        self._user()
        if type(page) is not int or type(per_page) is not int or not 1 <= page <= 1000 or not 1 <= per_page <= 50:
            raise ValueError('Invalid pagination')
        query = self._owned()
        total = query.count()
        rows = query.order_by(AIConnection.created_at.desc(), AIConnection.id.desc()).offset((page-1)*per_page).limit(per_page).all()
        return {'connections': [self._serialize(row) for row in rows], 'total': total,
                'page': page, 'per_page': per_page}

    def revoke(self, ident):
        self._user()
        row = self._connection(ident, lock=True)
        if row.revoked_at is None:
            row.revoked_at = datetime.utcnow()
            self.session.flush()
        return self._serialize(row)

    def require_permissions(self, ident, *, client_id, resource, scopes):
        """Recheck stored consent; ONLY after a future adapter validates a token.

        A grant ID is not authentication. This returns no Principal, accepts no
        token, is not exposed over HTTP and must not be used as a token verifier.
        Existing CRM record permissions must still be applied by the tool service.
        """
        user = self._user()
        row = self._connection(ident)
        client = self._client(client_id)
        required = scope_set(scopes)
        approved = scope_set(row.scopes)
        if (row.revoked_at is not None or row.expires_at <= datetime.utcnow()
                or row.client_id != client.id or row.resource != resource or resource != resource_uri()
                or not required <= (approved & scope_set(client.allowed_scopes) & self._permitted(user))):
            raise PermissionError('AI connection permission denied')
        return frozenset(required)
