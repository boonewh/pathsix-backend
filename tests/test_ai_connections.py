import asyncio
import json
import os
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from app.models import AIClient, AIConnection, User, Tenant
from app.services.ai_connections import AIConnectionService, ConnectionLimit, READ_SCOPES
from app.services.principal import Principal
from app.services.errors import RecordNotFound
from app.services.database_context import bind_principal
from app.utils import auth_utils
from test_security_boundaries import crm, rls_runtime

A = Principal(1, 1, frozenset({'admin'}))
ORDINARY = Principal(3, 1, frozenset())
B = Principal(2, 2, frozenset({'admin'}))
RESOURCE = 'https://example.test/mcp'


@pytest.fixture
def consent(crm, monkeypatch):
    monkeypatch.setenv('MCP_RESOURCE_URI', RESOURCE)
    with crm[1]() as db:
        db.add(AIClient(id='test-ai', name='Test AI', is_active=True, allowed_scopes=list(READ_SCOPES)))
        db.add(AIClient(id='disabled-ai', name='Disabled', is_active=False, allowed_scopes=['clients:read']))
        db.commit()
    return crm


def approve(service, scopes=None):
    return service.consent({'client_id': 'test-ai', 'scopes': scopes or ['clients:read'], 'approved': True})


@pytest.mark.parametrize('restricted', [False, True])
def test_consent_lifecycle_owner_scopes_and_irreversible_revocation(consent, restricted):
    factory = auth_utils.SessionLocal if restricted else consent[1]
    with factory() as db:
        service = AIConnectionService(db, A)
        preview = service.preview({'client_id': 'test-ai', 'scopes': ['clients:read']})
        assert preview['client_name'] == 'Test AI' and preview['resource'] == RESOURCE
        assert service.list()['total'] == 0  # Preview is read-only.
        result = approve(service)
        ident = result['id']
        assert result['scopes'] == ['clients:read'] and result['status'] == 'active'
        assert not {'token', 'access_token', 'refresh_token', 'password_hash', 'tenant_id', 'user_id'} & result.keys()
        db.commit()
        assert service.require_permissions(ident, client_id='test-ai', resource=RESOURCE, scopes=['clients:read']) == frozenset({'clients:read'})
        first = service.revoke(ident)
        db.commit()
        assert service.revoke(ident)['revoked_at'] == first['revoked_at']
        with pytest.raises(PermissionError):
            service.require_permissions(ident, client_id='test-ai', resource=RESOURCE, scopes=['clients:read'])
        assert approve(service)['id'] != ident
        db.rollback()
        assert service.list()['total'] == 1
    for principal in (ORDINARY, B):
        with factory() as db:
            service = AIConnectionService(db, principal)
            assert service.list()['total'] == 0
            for guessed in (ident, str(uuid4()), 'invalid'):
                with pytest.raises(RecordNotFound, match='AI connection not found'): service.revoke(guessed)


@pytest.mark.parametrize('data', [None, [], {}, {'approved': True},
    {'client_id': 'test-ai', 'scopes': ['clients:read'], 'approved': False},
    {'client_id': 'test-ai', 'scopes': ['clients:read'], 'approved': 1},
    {'client_id': 'test-ai', 'scopes': ['clients:read'], 'approved': True, 'tenant_id': 2},
    {'client_id': 'test-ai', 'scopes': ['clients:read'], 'approved': True, 'user_id': 2},
    {'client_id': 'test-ai', 'scopes': [], 'approved': True},
    {'client_id': 'test-ai', 'scopes': ['clients:write'], 'approved': True},
    {'client_id': 'test-ai', 'scopes': ['*'], 'approved': True},
    {'client_id': 'test-ai', 'scopes': ['clients:read', 'clients:read'], 'approved': True},
    {'client_id': 'test-ai', 'scopes': 'clients:read', 'approved': True},
    {'client_id': 'test-ai', 'scopes': [True], 'approved': True}])
def test_consent_requires_explicit_narrow_approval(consent, data):
    with auth_utils.SessionLocal() as db:
        service = AIConnectionService(db, A)
        with pytest.raises(ValueError): service.consent(data)
        assert service.list()['total'] == 0


@pytest.mark.parametrize('change', ['user', 'tenant', 'client', 'client_scopes', 'role', 'revoked'])
def test_current_state_revokes_stale_consent_immediately(consent, change):
    with auth_utils.SessionLocal() as db:
        service = AIConnectionService(db, A)
        ident = approve(service, ['reports:read'])['id']
        db.commit()
        assert service.require_permissions(ident, client_id='test-ai', resource=RESOURCE, scopes=['reports:read'])
        db.commit()
        with consent[1]() as admin:
            if change == 'user': admin.get(User, 1).is_active = False
            elif change == 'tenant': admin.get(Tenant, 1).is_active = False
            elif change == 'client': admin.get(AIClient, 'test-ai').is_active = False
            elif change == 'client_scopes': admin.get(AIClient, 'test-ai').allowed_scopes = ['clients:read']
            elif change == 'role': admin.get(User, 1).roles = []
            elif change == 'revoked': admin.get(AIConnection, ident).revoked_at = datetime.utcnow()
            admin.commit()
        with pytest.raises((PermissionError, RecordNotFound)):
            service.require_permissions(ident, client_id='test-ai', resource=RESOURCE, scopes=['reports:read'])


@pytest.mark.parametrize('wrong', ['client', 'resource', 'scope', 'server_resource'])
def test_client_resource_and_exact_scope_are_bound(consent, monkeypatch, wrong):
    with auth_utils.SessionLocal() as db:
        service = AIConnectionService(db, A)
        ident = approve(service)['id']
        db.commit()
        values = {'client_id': 'test-ai', 'resource': RESOURCE, 'scopes': ['clients:read']}
        if wrong == 'client': values['client_id'] = 'disabled-ai'
        elif wrong == 'resource': values['resource'] = RESOURCE + '/other'
        elif wrong == 'scope': values['scopes'] = ['projects:read']
        else: monkeypatch.setenv('MCP_RESOURCE_URI', RESOURCE + '/changed')
        with pytest.raises((PermissionError, RecordNotFound)):
            service.require_permissions(ident, **values)


def test_current_roles_not_principal_claims_control_reports(consent):
    with auth_utils.SessionLocal() as db:
        service = AIConnectionService(db, Principal(3, 1, frozenset({'admin'})))
        assert 'reports:read' not in service.catalog()['scopes']
        with pytest.raises(PermissionError): approve(service, ['reports:read'])
        assert approve(service)['status'] == 'active'


def test_disabled_client_can_still_be_revoked_and_history_is_retained(consent):
    with auth_utils.SessionLocal() as db:
        ident = approve(AIConnectionService(db, A))['id']
        db.commit()
    with consent[1]() as admin:
        admin.get(AIClient, 'test-ai').is_active = False
        admin.commit()
    with auth_utils.SessionLocal() as db:
        service = AIConnectionService(db, A)
        assert service.catalog()['clients'] == []
        assert service.revoke(ident)['status'] == 'revoked'
        db.commit()
        assert service.list()['total'] == 1


def test_limits_rollback_pagination_and_configuration(consent, monkeypatch):
    from app.services.ai_connections import AIConfigurationError
    monkeypatch.setattr('app.services.ai_connections.MAX_ACTIVE_CONNECTIONS', 2)
    with auth_utils.SessionLocal() as db:
        service = AIConnectionService(db, A)
        first = approve(service)['id']
        approve(service)
        db.commit()
        with pytest.raises(ConnectionLimit): approve(service)
        assert service.list(1, 1)['total'] == 2
        assert service.list(1, 1)['connections'][0]['id'] != service.list(2, 1)['connections'][0]['id']
        for args in ((0,20), (True,20), (1,51), (1001,20)):
            with pytest.raises(ValueError): service.list(*args)
        service.revoke(first)
        approve(service)
        db.rollback()
        assert service.list()['total'] == 2
        for value in ('', 'http://example.test/mcp', 'https://u:p@example.test/mcp', RESOURCE+'#part'):
            monkeypatch.setenv('MCP_RESOURCE_URI', value)
            with pytest.raises(AIConfigurationError): service.preview({'client_id':'test-ai','scopes':['clients:read']})


def test_revocation_does_not_reset_daily_creation_limit(consent, monkeypatch):
    monkeypatch.setattr('app.services.ai_connections.MAX_DAILY_CONSENTS',1)
    with auth_utils.SessionLocal() as db:
        service=AIConnectionService(db,A)
        ident=approve(service)['id']
        service.revoke(ident)
        db.commit()
        with pytest.raises(ConnectionLimit,match='Daily'): approve(service)
        assert service.revoke(ident)['status']=='revoked'


def test_http_consent_is_owner_only_and_returns_no_credentials(consent):
    call, _, _ = consent
    for path in ('/api/ai-connections', '/api/ai-connections/clients'):
        assert call('GET', path, user=None)[0] == 401
        assert call('GET', path)[0] == 200
    assert call('GET','/api/ai-connections?page=oops')[0] == 400
    request = {'client_id':'test-ai','scopes':['clients:read']}
    assert call('POST','/api/ai-connections/preview',request)[0] == 200
    assert call('POST','/api/ai-connections/consent',request)[0] == 400
    status, body = call('POST','/api/ai-connections/consent',dict(request,approved=True),user=3)
    assert status == 201
    ident = json.loads(body)['id']
    for user in (1,2):  # Even a same-tenant administrator cannot manage another user's grant.
        assert call('DELETE',f'/api/ai-connections/{ident}',user=user) == call('DELETE',f'/api/ai-connections/{uuid4()}',user=user)
        assert json.loads(call('GET','/api/ai-connections',user=user)[1])['total'] == 0
    assert call('DELETE',f'/api/ai-connections/{ident}',user=3)[0] == 200
    assert json.loads(call('GET','/api/ai-connections',user=3)[1])['connections'][0]['status'] == 'revoked'
    assert 'access_token' not in body and 'refresh_token' not in body


@pytest.mark.parametrize('operation', ['commit', 'rollback'])
def test_owner_rls_pool_reuse_and_runtime_privileges(rls_runtime, monkeypatch, operation):
    monkeypatch.setenv('MCP_RESOURCE_URI',RESOURCE)
    runtime, admin, _ = rls_runtime
    with admin() as db:
        db.add(AIClient(id='test-ai',name='Test AI',is_active=True,allowed_scopes=['clients:read']))
        db.commit()
    ids = {}
    for principal in (A, ORDINARY, B):
        with runtime() as db:
            ids[principal.user_id] = approve(AIConnectionService(db,principal))['id']
            db.commit()
    for principal in (A, ORDINARY, B, None, A):
        with runtime() as db:
            if principal: bind_principal(db,principal)
            rows=db.execute(text('SELECT id FROM ai_connections')).scalars().all()
            assert rows == ([] if principal is None else [ids[principal.user_id]])
            assert db.execute(text("SELECT NULLIF(current_setting('crm.user_id',true),'')")).scalar_one() == (str(principal.user_id) if principal else None)
            getattr(db,operation)()
    for statement in ("UPDATE ai_clients SET is_active=true", "DELETE FROM ai_connections"):
        with runtime() as db:
            bind_principal(db,A)
            with pytest.raises(DBAPIError) as exc: db.execute(text(statement))
            assert exc.value.orig.pgcode == '42501'
    with runtime() as db:
        bind_principal(db,A)
        assert db.execute(text('UPDATE ai_connections SET revoked_at=now() WHERE id=:id'),{'id':ids[3]}).rowcount == 0
        with pytest.raises(DBAPIError) as exc:
            db.execute(text('UPDATE ai_connections SET user_id=3 WHERE id=:id'),{'id':ids[1]})
        assert exc.value.orig.pgcode in {'42501','23514'}


def test_expired_consent_denied_without_rewriting_history(consent):
    ident=str(uuid4())
    with consent[1]() as db:
        db.add(AIConnection(id=ident,tenant_id=1,user_id=1,client_id='test-ai',resource=RESOURCE,
                            scopes=['clients:read'],created_at=datetime.utcnow()-timedelta(days=2),
                            expires_at=datetime.utcnow()-timedelta(days=1)))
        db.commit()
    with auth_utils.SessionLocal() as db:
        service=AIConnectionService(db,A)
        assert service.list()['connections'][0]['status']=='expired'
        with pytest.raises(PermissionError): service.require_permissions(ident,client_id='test-ai',resource=RESOURCE,scopes=['clients:read'])


def test_database_consent_is_immutable_and_revocation_terminal(consent):
    if consent[1].kw['bind'].dialect.name != 'postgresql':
        pytest.skip('Requires PostgreSQL immutable consent trigger')
    with auth_utils.SessionLocal() as db:
        service=AIConnectionService(db,A)
        ident=approve(service)['id']
        db.commit()
        statements=["scopes='[\"projects:read\"]'", "resource='https://other.test/mcp'",
                    "expires_at=expires_at + interval '1 day'", 'user_id=3']
        for change in statements:
            with db.begin_nested() as sp:
                with pytest.raises(DBAPIError) as exc:
                    db.execute(text('UPDATE ai_connections SET '+change+' WHERE id=:id'),{'id':ident})
                assert exc.value.orig.pgcode=='23514'
                sp.rollback()
        service.revoke(ident)
        db.commit()
        with pytest.raises(DBAPIError) as exc:
            db.execute(text('UPDATE ai_connections SET revoked_at=NULL WHERE id=:id'),{'id':ident})
        assert exc.value.orig.pgcode=='23514'


def test_new_owner_relation_rejects_cross_tenant_even_for_operator(consent):
    if consent[1].kw['bind'].dialect.name != 'postgresql':
        pytest.skip('Requires PostgreSQL foreign keys')
    with consent[1]() as db:
        row=AIConnection(id=str(uuid4()),tenant_id=1,user_id=2,client_id='test-ai',resource=RESOURCE,
                         scopes=['clients:read'],expires_at=datetime.utcnow()+timedelta(days=1))
        db.add(row)
        with pytest.raises(DBAPIError) as exc: db.flush()
        assert exc.value.orig.pgcode == '23503'


def test_concurrent_consent_creation_cannot_exceed_owner_limit(consent, monkeypatch):
    if consent[1].kw['bind'].dialect.name != 'postgresql':
        pytest.skip('Requires PostgreSQL row locks')
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    monkeypatch.setattr('app.services.ai_connections.MAX_ACTIVE_CONNECTIONS',1)
    barrier=Barrier(2)
    factory=auth_utils.SessionLocal
    def create():
        with factory() as db:
            barrier.wait(timeout=10)
            try:
                approve(AIConnectionService(db,A))
                db.commit()
                return 'created'
            except ConnectionLimit:
                db.rollback()
                return 'limited'
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(create) for _ in range(2)]
        assert sorted(f.result(timeout=20) for f in futures)==['created','limited']
    with factory() as db: assert AIConnectionService(db,A).list()['total']==1
