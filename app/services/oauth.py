"""Authlib protocol adapter and transactional opaque-token lifecycle."""
from datetime import datetime, timedelta
from dataclasses import dataclass
import hashlib
import logging
import re
import secrets
from urllib.parse import urlsplit

from sqlalchemy import text
from sqlalchemy.orm import joinedload
from authlib.oauth2.rfc6749 import AuthorizationServer, OAuth2Request
from authlib.oauth2.rfc6749.requests import BasicOAuth2Payload
from authlib.oauth2.rfc6749.grants import AuthorizationCodeGrant, RefreshTokenGrant
from authlib.oauth2.rfc6749.errors import InvalidGrantError, InvalidRequestError, InvalidScopeError
from authlib.oauth2.rfc7636 import CodeChallenge
from authlib.oauth2.rfc7636.challenge import create_s256_code_challenge
from app.models import AIClient, AIConnection, OAuthCredential, User
from app.services.principal import Principal
from app.services.ai_connections import AIConnectionService, READ_SCOPES, resource_uri, scope_set
from app.services.database_context import auth_lookup, bind_principal
from app.services.errors import RecordNotFound

# Authlib debug records can include issued credentials. Never enable them here.
logging.getLogger('authlib.oauth2').setLevel(logging.WARNING)
ACCESS_SECONDS = 300
REFRESH_SECONDS = 86400 * 7
CODE_SECONDS = 120


def digest(value):
    return hashlib.sha256(value.encode('ascii')).hexdigest()


def valid_secret(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{43,128}', value) is not None


def issuer():
    parsed = urlsplit(resource_uri())
    return f'{parsed.scheme}://{parsed.netloc}'


def valid_redirect(value):
    if not isinstance(value, str) or len(value) > 1000:
        return False
    try:
        p = urlsplit(value)
        p.port
        return bool(p.scheme == 'https' and p.hostname and not p.username and not p.password
                    and not p.fragment and not p.query and not any(ord(c) < 33 or c == '\\' for c in value))
    except ValueError:
        return False


class Client:
    def __init__(self, row): self.row = row
    def get_client_id(self): return self.row.id
    def get_default_redirect_uri(self): return None  # Explicit redirect required.
    def check_redirect_uri(self, uri): return valid_redirect(uri) and uri in self.row.redirect_uris
    def check_response_type(self, value): return value == 'code'
    def check_grant_type(self, value): return value in ('authorization_code', 'refresh_token')
    def check_endpoint_auth_method(self, method, endpoint): return method == 'none'
    def check_client_secret(self, secret): return False
    def get_allowed_scope(self, value):
        scopes = parse_scope(value)
        if not scopes <= scope_set(self.row.allowed_scopes): raise InvalidScopeError()
        return ' '.join(sorted(scopes))


def parse_scope(value):
    try:
        if not isinstance(value, str) or len(value) > 200: raise ValueError()
        return scope_set(value.split(' '))
    except ValueError:
        raise InvalidScopeError() from None


class ProtocolRequest(OAuth2Request):
    def __init__(self, data, method='POST'):
        super().__init__(method, issuer() + ('/oauth/authorize' if method == 'GET' else '/oauth/token'))
        self.payload = BasicOAuth2Payload(data)
    @property
    def args(self): return self.payload.data
    @property
    def form(self): return self.payload.data


class Credential:
    def __init__(self, row, client_id):
        self.row, self.client_id = row, client_id
        self.code_challenge = row.code_challenge
        self.code_challenge_method = 'S256'
    def get_redirect_uri(self): return self.row.redirect_uri
    def get_scope(self): return ' '.join(self.row.scopes)
    def check_client(self, client): return self.client_id == client.get_client_id()


class S256Only(CodeChallenge):
    SUPPORTED_CODE_CHALLENGE_METHOD = ['S256']


class CodeGrant(AuthorizationCodeGrant):
    TOKEN_ENDPOINT_AUTH_METHODS = ['none']
    def generate_authorization_code(self): return secrets.token_urlsafe(32)
    def save_authorization_code(self, code, request):
        self.server.save_code(code, request)
    def query_authorization_code(self, code, client):
        return self.server.find_credential(code, 'code', client, self.request.form)
    def authenticate_user(self, credential): return self.server.validate_credential(credential)
    def delete_authorization_code(self, credential): credential.row.used_at = datetime.utcnow()


class RefreshGrant(RefreshTokenGrant):
    TOKEN_ENDPOINT_AUTH_METHODS = ['none']
    INCLUDE_NEW_REFRESH_TOKEN = True
    def authenticate_refresh_token(self, value):
        return self.server.find_credential(value, 'refresh', self.request.client, self.request.form)
    def authenticate_user(self, credential): return self.server.validate_credential(credential)
    def revoke_old_credential(self, credential): credential.row.used_at = datetime.utcnow()


class Server(AuthorizationServer):
    def __init__(self, db, principal=None, intent_hash=None):
        super().__init__(scopes_supported=list(READ_SCOPES))
        self.db, self.principal, self.intent_hash = db, principal, intent_hash
        if principal is not None:
            bind_principal(db, principal)
        self.connection = None
        self.register_grant(CodeGrant, [S256Only(required=True)])
        self.register_grant(RefreshGrant)
        self.register_token_generator('default', self.generate)
    def create_oauth2_request(self, request): return request
    def handle_response(self, status, body, headers): return status, body, headers
    def send_signal(self, *args, **kwargs): pass
    def query_client(self, ident):
        row = self.db.query(AIClient).populate_existing().filter_by(id=ident, is_active=True, oauth_enabled=True).first()
        if row is None or not isinstance(row.redirect_uris, list) or not row.redirect_uris:
            return None
        if not all(valid_redirect(uri) for uri in row.redirect_uris): return None
        return Client(row)
    def validate_authorization(self, data):
        # ChatGPT sends this optional UI language hint. Our consent UI currently
        # uses English; ignore the hint without relaxing any OAuth requirements.
        data = {key: value for key, value in data.items() if key != 'ui_locales'}
        if (set(data) != {'client_id','redirect_uri','response_type','scope','state','resource','code_challenge','code_challenge_method'}
                or data['resource'] != resource_uri() or data['code_challenge_method'] != 'S256'
                or not re.fullmatch(r'[A-Za-z0-9_-]{43}', data['code_challenge'])
                or not 1 <= len(data['state']) <= 512):
            raise InvalidRequestError()
        parse_scope(data['scope'])
        request = ProtocolRequest(data, 'GET')
        grant = self.get_consent_grant(request)
        request.client.get_allowed_scope(data['scope'])
        return request, grant
    def save_code(self, code, request):
        if self.principal is None or not self.intent_hash: raise InvalidGrantError()
        result = AIConnectionService(self.db, self.principal).consent({
            'client_id': request.client.get_client_id(), 'scopes': request.payload.scope.split(), 'approved': True})
        self.connection = self.db.get(AIConnection, result['id'])
        self.db.add(OAuthCredential(token_hash=digest(code), tenant_id=self.principal.tenant_id,
            user_id=self.principal.user_id, connection_id=result['id'], kind='code', scopes=result['scopes'],
            expires_at=datetime.utcnow()+timedelta(seconds=CODE_SECONDS),
            redirect_uri=request.payload.redirect_uri, code_challenge=request.payload.data['code_challenge'],
            intent_hash=self.intent_hash))
        self.db.flush()  # Unique intent prevents double approval/replayed consent.
    def find_credential(self, raw, kind, client, data):
        if self.principal is None or not valid_secret(raw) or data.get('resource') != resource_uri(): return None
        row = self.db.query(OAuthCredential).populate_existing().filter_by(token_hash=digest(raw),
            kind=kind, tenant_id=self.principal.tenant_id, user_id=self.principal.user_id).first()
        if row is None: return None
        # All exchanges/revocations lock the grant first, then its credential.
        connection = self.db.query(AIConnection).populate_existing().filter_by(id=row.connection_id,
            tenant_id=self.principal.tenant_id, user_id=self.principal.user_id,
            client_id=client.get_client_id(), resource=data['resource']).with_for_update().first()
        if connection is None: return None
        row = self.db.query(OAuthCredential).populate_existing().filter_by(token_hash=row.token_hash).with_for_update().one()
        self.connection = connection
        if row.expires_at <= datetime.utcnow(): return None
        if kind == 'code':
            verifier = data.get('code_verifier','')
            if (row.redirect_uri != data.get('redirect_uri') or not re.fullmatch(r'[A-Za-z0-9._~-]{43,128}',verifier)
                    or not secrets.compare_digest(create_s256_code_challenge(verifier), row.code_challenge)):
                return None
        if row.used_at is not None:
            if connection.revoked_at is None: connection.revoked_at = datetime.utcnow()
            return None  # Route commits replay revocation even on invalid_grant.
        return Credential(row, client.get_client_id())
    def validate_credential(self, credential):
        try:
            AIConnectionService(self.db, self.principal).require_permissions(self.connection.id,
                client_id=self.connection.client_id, resource=self.connection.resource, scopes=credential.row.scopes)
        except (PermissionError, ValueError, RecordNotFound):
            raise InvalidGrantError() from None
        return self.principal
    def generate(self, grant_type, client, user=None, scope=None, **kwargs):
        remaining = int((self.connection.expires_at-datetime.utcnow()).total_seconds())
        if remaining <= 0: raise InvalidGrantError()
        recent = self.db.query(OAuthCredential).filter_by(connection_id=self.connection.id).filter(
            OAuthCredential.kind=='access', OAuthCredential.created_at > datetime.utcnow()-timedelta(minutes=1)).count()
        if recent >= 20: raise InvalidRequestError('Token exchange limit reached')
        return {'token_type':'Bearer', 'access_token':secrets.token_urlsafe(32),
                'refresh_token':secrets.token_urlsafe(32), 'expires_in':min(ACCESS_SECONDS,remaining),
                'scope':scope, 'resource':self.connection.resource}
    def save_token(self, token, request):
        now = datetime.utcnow()
        for kind, lifetime in (('access',token['expires_in']), ('refresh',REFRESH_SECONDS)):
            self.db.add(OAuthCredential(token_hash=digest(token[kind+'_token']), kind=kind,
                tenant_id=self.principal.tenant_id,user_id=self.principal.user_id,connection_id=self.connection.id,
                scopes=sorted(parse_scope(token['scope'])),created_at=now,
                expires_at=min(now+timedelta(seconds=lifetime),self.connection.expires_at)))
        self.db.flush()


def lookup_principal(factory, raw):
    """Narrow bootstrap returns only IDs for a presented high-entropy credential."""
    if not valid_secret(raw): return None
    hashed = digest(raw)
    with factory() as db:
        if db.get_bind().dialect.name == 'postgresql':
            row = db.execute(text('SELECT * FROM crm_oauth_identity(:hash)'), {'hash':hashed}).first()
        else:  # Isolated SQLite tests only; production requires PostgreSQL RLS.
            row = db.query(OAuthCredential.user_id,OAuthCredential.tenant_id).filter_by(token_hash=hashed).first()
    if row is None: return None
    with factory() as db:
        auth_lookup(db,user_id=row[0])
        user = db.query(User).options(joinedload(User.roles),joinedload(User.tenant)).filter_by(id=row[0],tenant_id=row[1]).first()
        if user is None or not user.is_active or not user.tenant.is_active: return None
        return Principal(user.id,user.tenant_id,frozenset(role.name for role in user.roles))


@dataclass(frozen=True)
class DelegatedIdentity:
    user_id: int
    tenant_id: int
    connection_id: str
    client_id: str
    scopes: frozenset


def validate_access(db, principal, raw, resource, required_scopes=None):
    """Internal resource-server boundary; not accepted by existing web services."""
    if principal is None or not valid_secret(raw): raise InvalidGrantError()
    service = AIConnectionService(db, principal)
    row = db.query(OAuthCredential).populate_existing().filter_by(token_hash=digest(raw),kind='access',
        tenant_id=principal.tenant_id,user_id=principal.user_id).first()
    if row is None or row.used_at is not None or row.expires_at <= datetime.utcnow(): raise InvalidGrantError()
    required_scopes = row.scopes if required_scopes is None else required_scopes
    required = scope_set(required_scopes)
    if not required <= scope_set(row.scopes): raise InvalidScopeError()
    try:
        grant = service._connection(row.connection_id)
        service.require_permissions(grant.id,client_id=grant.client_id,resource=resource,scopes=required_scopes)
    except (PermissionError, RecordNotFound, ValueError):
        raise InvalidGrantError() from None
    client = db.query(AIClient).filter_by(id=grant.client_id,oauth_enabled=True).first()
    if client is None: raise InvalidGrantError()
    return DelegatedIdentity(principal.user_id,principal.tenant_id,grant.id,grant.client_id,frozenset(required))
