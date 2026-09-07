import asyncio
import importlib
import time
import os
import uuid
from datetime import datetime

import pytest
from authlib.jose import jwt
from quart import Quart
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Tenant, Role, User, Client, Lead, Project, Contact, Account, Interaction
from app.routes import register_blueprints
from app.utils import auth_utils


@pytest.fixture
def crm(tmp_path, monkeypatch):
    url = os.getenv('SECURITY_TEST_DATABASE_URL')
    schema = 'security_test_' + uuid.uuid4().hex
    admin_engine = create_engine(url) if url else None
    if admin_engine:
        with admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(url, connect_args={'options': f'-csearch_path={schema}'})
    else:
        engine = create_engine(f"sqlite:///{tmp_path / 'security.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    for name in ('accounts', 'contacts', 'projects', 'interactions', 'clients', 'auth', 'reports', 'imports', 'users', 'search'):
        monkeypatch.setattr(importlib.import_module(f'app.routes.{name}'), 'SessionLocal', factory)
    monkeypatch.setattr(auth_utils, 'SessionLocal', factory)
    with factory() as db:
        db.add_all([Tenant(id=1, name='A', slug='a'), Tenant(id=2, name='B', slug='b')])
        admin = Role(name='admin')
        db.add_all([User(id=1, tenant_id=1, email='a@example.test', password_hash='unused', roles=[admin]),
                    User(id=2, tenant_id=2, email='b@example.test', password_hash='unused', roles=[admin]),
                    User(id=3, tenant_id=1, email='ordinary@example.test', password_hash='unused')])
        db.flush()
        for tenant in (1, 2):
            db.add(Client(id=tenant, tenant_id=tenant, created_by=tenant, name=f'Private client {tenant}'))
            db.add(Lead(id=tenant, tenant_id=tenant, created_by=tenant, name=f'Private lead {tenant}'))
            db.add(Project(id=tenant, tenant_id=tenant, created_by=tenant, project_name=f'Private project {tenant}', project_status='pending'))
        db.flush()
        db.add_all([Account(id=1, tenant_id=1, client_id=1, account_number='A'),
                    Contact(id=1, tenant_id=1, client_id=1, first_name='A'),
                    Interaction(id=1, tenant_id=1, client_id=1, summary='A', follow_up=datetime(2026, 9, 10)),
                    Interaction(id=2, tenant_id=2, client_id=2, summary='B', follow_up=datetime(2026, 9, 10))])
        db.commit()
        if admin_engine:
            # Explicit fixture IDs do not advance PostgreSQL sequences (unlike
            # SQLite). Keep subsequent API-created rows clear of those IDs.
            for model in (Tenant, Role, User, Client, Lead, Project, Account, Contact, Interaction):
                table = model.__tablename__
                db.execute(text(
                    f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                    f"(SELECT MAX(id) FROM {table}))"
                ))
            db.commit()
    app = Quart(__name__)
    app.config['SECRET_KEY'] = 'test-only-signing-key'
    register_blueprints(app)

    def call(method, path, body=None, user=1, claims=None):
        payload = {'sub': user, 'exp': int(time.time()) + 300, 'roles': ['admin']}
        payload.update(claims or {})
        token = jwt.encode({'alg': 'HS256'}, payload, app.config['SECRET_KEY']).decode()
        async def run():
            response = await app.test_client().open(path, method=method, json=body,
                headers={'Authorization': f'Bearer {token}'} if user else {})
            return response.status_code, await response.get_data(as_text=True)
        return asyncio.run(run())
    yield call, factory, app
    engine.dispose()
    if admin_engine:
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


@pytest.mark.parametrize('resource,body', [
    ('accounts', {'account_number': 'new', 'client_id': 2}),
    ('contacts', {'first_name': 'new', 'client_id': 2}),
    ('contacts', {'first_name': 'new', 'lead_id': 2}),
    ('projects', {'project_name': 'new', 'client_id': 2}),
    ('projects', {'project_name': 'new', 'lead_id': 2}),
    ('clients', {'name': 'new', 'source_lead_id': 2}),
    ('interactions', {'summary': 'new', 'contact_date': '2026-09-06T12:00:00', 'project_id': 2}),
])
def test_cross_tenant_create(crm, resource, body):
    call, _, _ = crm
    assert call('POST', f'/api/{resource}', body)[0] == 404
    missing = {key: 99999 if key.endswith('_id') else value for key, value in body.items()}
    assert call('POST', f'/api/{resource}', missing)[0] == 404
    own = {key: 1 if key.endswith('_id') else value for key, value in body.items()}
    assert call('POST', f'/api/{resource}', own, user=3)[0] == 404


@pytest.mark.parametrize('resource,model,body', [
    ('accounts', Account, {'client_id': 2}),
    ('contacts', Contact, {'client_id': 2}),
    ('contacts', Contact, {'client_id': None, 'lead_id': 2}),
    ('projects', Project, {'client_id': 2}),
    ('projects', Project, {'lead_id': 2}),
    ('interactions', Interaction, {'client_id': 2}),
    ('interactions', Interaction, {'client_id': None, 'lead_id': 2}),
    ('interactions', Interaction, {'client_id': None, 'project_id': 2}),
])
def test_cross_tenant_update_is_atomic(crm, resource, model, body):
    call, factory, _ = crm
    with factory() as db:
        before = {key: getattr(db.get(model, 1), key) for key in body}
    assert call('PUT', f'/api/{resource}/1', body)[0] == 404
    with factory() as db:
        assert {key: getattr(db.get(model, 1), key) for key in body} == before


@pytest.mark.parametrize('resource,body', [
    ('contacts', {'first_name': 'new'}),
    ('contacts', {'first_name': 'new', 'client_id': 1, 'lead_id': 1}),
    ('projects', {'project_name': 'new', 'client_id': 1, 'lead_id': 1}),
    ('interactions', {'summary': 'new', 'contact_date': '2026-09-06', 'client_id': 1, 'lead_id': 1}),
])
def test_parent_combinations(crm, resource, body):
    assert crm[0]('POST', f'/api/{resource}', body)[0] == 400


@pytest.mark.parametrize('resource', ['contacts', 'interactions'])
def test_partial_update_validates_retained_parent(crm, resource):
    assert crm[0]('PUT', f'/api/{resource}/1', {'lead_id': 1})[0] == 400
    assert crm[0]('PUT', f'/api/{resource}/1', {'client_id': None})[0] == 400
    assert crm[0]('PUT', f'/api/{resource}/1', {'client_id': None, 'lead_id': 1})[0] == 200


def test_calendar_and_transfer(crm):
    call, factory, _ = crm
    assert call('GET', '/api/interactions/1/calendar.ics', user=None)[0] == 401
    assert call('GET', '/api/interactions/2/calendar.ics')[0] == 404
    assert call('GET', '/api/interactions/1/calendar.ics', user=3)[0] == 404
    status, data = call('GET', '/api/interactions/1/calendar.ics')
    assert status == 200 and 'BEGIN:VCALENDAR' in data
    assert call('POST', '/api/interactions/transfer', {'from_lead_id': 1, 'to_client_id': 2})[0] == 404
    assert call('POST', '/api/interactions/transfer', {'from_lead_id': 2, 'to_client_id': 1})[0] == 404
    assert call('POST', '/api/interactions/transfer', {'from_lead_id': 1, 'to_client_id': 1}, user=3)[0] == 404


def test_live_authorization_and_expiry(crm):
    call, factory, _ = crm
    assert call('GET', '/api/me', claims={'exp': 1})[0] == 401
    with factory() as db:
        db.get(User, 1).roles = []
        db.commit()
    assert call('GET', '/api/users')[0] == 403
    with factory() as db:
        db.get(Tenant, 1).is_active = False
        db.commit()
    assert call('GET', '/api/me')[0] == 403
    with factory() as db:
        db.get(Tenant, 1).is_active = True
        db.get(User, 1).is_active = False
        db.commit()
    assert call('GET', '/api/me')[0] == 401


@pytest.mark.parametrize('method,path', [('GET',''), ('POST',''), ('GET','/1/status'),
    ('POST','/1/restore'), ('DELETE','/1'), ('GET','/restores')])
def test_backup_api_is_not_registered(crm, method, path):
    assert crm[0](method, '/api/admin/backups' + path)[0] == 404


def test_reports_and_imports_require_current_admin(crm):
    call, _, app = crm
    for rule in app.url_map.iter_rules():
        if rule.endpoint.startswith(('reports.', 'imports.')):
            path = str(rule)
            if '<' in path:
                continue
            method = next(m for m in ('GET', 'POST', 'PUT', 'DELETE') if m in rule.methods)
            assert call(method, path, {}, user=3)[0] == 403, path


def test_restore_and_parent_inheritance(crm):
    call, factory, _ = crm
    for method, path in [('GET', '/api/accounts/1'), ('DELETE', '/api/accounts/1'),
                         ('PUT', '/api/contacts/1'), ('DELETE', '/api/contacts/1'),
                         ('GET', '/api/projects/1'), ('PUT', '/api/projects/1')]:
        assert call(method, path, {}, user=3)[0] == 404
    assert call('GET', '/api/accounts', user=3) == (200, '[]\n')
    assert call('GET', '/api/contacts?client_id=1', user=3)[0] == 404
    with factory() as db:
        db.get(Client, 1).deleted_at = datetime.utcnow()
        db.commit()
    assert call('PUT', '/api/clients/1/restore', user=3)[0] == 404
    assert call('PUT', '/api/clients/1/restore')[0] == 200


def test_malformed_relationships_do_not_load_other_tenant(crm):
    call, factory, _ = crm
    with factory() as db:
        db.get(Account, 1).client_id = 2
        db.get(Project, 1).client_id = 2
        db.get(Client, 1).source_lead_id = 2
        db.get(Interaction, 1).client_id = 2
        db.commit()
    for path in ('/api/accounts', '/api/projects/1', '/api/clients/1', '/api/interactions/all'):
        status, data = call('GET', path)
        assert status == 200, data
        assert 'Private client 2' not in data and 'Private lead 2' not in data
    assert call('GET', '/api/interactions/1/calendar.ics')[0] == 404


def test_every_data_route_requires_authentication(crm):
    import re
    call, _, app = crm
    public = {'auth.login', 'auth.forgot_password', 'auth.reset_password', 'static'}
    for rule in app.url_map.iter_rules():
        if rule.endpoint in public:
            continue
        path = re.sub(r'<[^>]+>', '1', str(rule))
        for method in sorted(rule.methods - {'HEAD', 'OPTIONS'}):
            assert call(method, path, {}, user=None)[0] == 401, (method, path)


@pytest.mark.parametrize('resource,body', [
    ('accounts', {'account_number': 'valid', 'client_id': 1}),
    ('contacts', {'first_name': 'valid', 'client_id': 1}),
    ('projects', {'project_name': 'valid', 'client_id': 1}),
    ('projects', {'project_name': 'standalone'}),
    ('clients', {'name': 'valid', 'source_lead_id': 1}),
    ('interactions', {'summary': 'valid', 'contact_date': '2026-09-06T12:00:00', 'client_id': 1}),
])
def test_valid_create_remains_available(crm, resource, body):
    assert crm[0]('POST', '/api/' + resource, body)[0] == 201


def test_search_service_enforces_tenant_without_http_context(crm):
    from app.services.principal import Principal
    from app.services.search import SearchService
    _, factory, _ = crm
    with factory() as db:
        for tenant in (1, 2):
            results = SearchService(db, Principal(tenant, tenant, frozenset({'admin'}))).search('Private')
            assert {(r['type'], r['id']) for r in results} == {('client', tenant), ('lead', tenant), ('project', tenant)}
        with pytest.raises(TypeError):
            SearchService(db, None)


def test_search_uses_assignment_and_parent_access(crm):
    import json
    from app.services.principal import Principal
    from app.services.search import SearchService
    call, factory, _ = crm
    with factory() as db:
        db.get(Client, 1).assigned_to = 3
        db.get(Lead, 1).assigned_to = 3
        db.get(Account, 1).account_name = 'Private account'
        db.get(Project, 1).client_id = 1
        db.commit()
        expected = {('client', 1), ('lead', 1), ('account', 1), ('project', 1)}
        results = SearchService(db, Principal(3, 1, frozenset())).search('Private')
        assert {(r['type'], r['id']) for r in results} == expected
    status, body = call('GET', '/api/search?q=Private&tenant_id=2', user=3)
    assert status == 200
    assert {(r['type'], r['id']) for r in json.loads(body)} == expected
    with factory() as db:
        # Direct assignment overrides parent inheritance and creator access.
        db.get(Project, 1).assigned_to = 1
        db.commit()
        assert not any(r['type'] == 'project' for r in SearchService(db, Principal(3, 1, frozenset())).search('Private'))
        db.get(Project, 1).assigned_to = 3
        db.get(Client, 1).assigned_to = None
        db.commit()
        results = SearchService(db, Principal(3, 1, frozenset())).search('Private')
        assert any(r['type'] == 'project' and r['link'] == '/projects/1' for r in results)
        assert not any(r['type'] in {'client', 'account'} for r in results)


def test_search_excludes_deleted_and_malformed_parents_without_http(crm):
    from app.services.principal import Principal
    from app.services.search import SearchService
    _, factory, _ = crm
    with factory() as db:
        db.get(Account, 1).account_name = 'Private account'
        db.get(Account, 1).client_id = 2
        db.get(Project, 1).client_id = 2
        db.commit()
        service = SearchService(db, Principal(1, 1, frozenset({'admin'})))
        assert {r['type'] for r in service.search('Private')} == {'client', 'lead'}
        db.get(Project, 1).client_id = 1
        db.get(Account, 1).client_id = 1
        db.get(Client, 1).deleted_at = datetime.utcnow()
        db.get(Lead, 1).deleted_at = datetime.utcnow()
        db.commit()
        assert service.search('Private') == []


def test_search_bounds_literal_wildcards_and_user_visibility(crm):
    from app.services.principal import Principal
    from app.services.search import SearchService
    call, factory, _ = crm
    with factory() as db:
        service = SearchService(db, Principal(1, 1, frozenset({'admin'})))
        assert service.search('   ') == []
        assert service.search('%') == []
        assert service.search('_') == []
        db.get(Client, 1).name = '100% private_value'
        db.commit()
        assert [r['id'] for r in service.search('%')] == [1]
        assert [r['id'] for r in service.search('_')] == [1]
        with pytest.raises(ValueError):
            service.search('x' * 201)
        with pytest.raises(ValueError):
            service.search('private', limit=21)
        # Ordinary users must not receive user-directory results, even with admin claims.
        assert SearchService(db, Principal(3, 1, frozenset())).search('example.test') == []
        assert len(service.search('example.test', limit=1)) == 1
    assert call('GET', '/api/search?q=' + 'x' * 201)[0] == 400
    assert call('GET', '/api/search?q=example.test', user=3)[1] == '[]\n'


def test_search_rechecks_current_roles_each_request(crm):
    import json
    call, factory, _ = crm
    assert len(json.loads(call('GET', '/api/search?q=example.test')[1])) == 2
    with factory() as db:
        db.get(User, 1).roles = []
        db.commit()
    assert call('GET', '/api/search?q=example.test')[1] == '[]\n'
    with factory() as db:
        db.get(Tenant, 1).is_active = False
        db.commit()
    assert call('GET', '/api/search?q=Private')[0] == 403


@pytest.mark.parametrize('operation', ['detail', 'update', 'delete', 'restore'])
def test_client_service_denies_other_tenant_without_http(crm, operation):
    from app.services.clients import ClientService, RecordNotFound
    from app.services.principal import Principal
    from app.schemas.clients import ClientUpdateSchema
    _, factory, _ = crm
    with factory() as db:
        service = ClientService(db, Principal(1, 1, frozenset({'admin'})))
        with pytest.raises(RecordNotFound):
            if operation == 'update':
                service.update(2, ClientUpdateSchema(name='leak'))
            else:
                getattr(service, operation)(2)
        db.commit()
    with factory() as db:
        assert db.get(Client, 2).name == 'Private client 2'
        assert db.get(Client, 2).deleted_at is None


def test_client_service_scopes_nested_data_and_does_not_commit(crm):
    from app.services.clients import ClientService
    from app.services.principal import Principal
    from app.schemas.clients import ClientCreateSchema, ClientUpdateSchema
    from app.models import ActivityLog
    _, factory, _ = crm
    with factory() as db:
        db.get(Client, 1).source_lead_id = 2
        db.add(Contact(tenant_id=2, client_id=1, first_name='Foreign'))
        db.commit()
    with factory() as db:
        service = ClientService(db, Principal(1, 1, frozenset({'admin'})))
        detail = service.detail(1)
        assert detail['lead_origin'] is None
        assert len(detail['contacts']) == 1
        assert 'Foreign' not in str(detail)
        assert db.query(ActivityLog).count() == 0  # Pure read, no implicit audit mutation.
        service.update(1, ClientUpdateSchema(name='rolled back'))
        db.rollback()
        assert service.detail(1)['name'] == 'Private client 1'
        new_id = service.create(ClientCreateSchema(name='rolled back create', tenant_id=2, created_by=2))
        assert db.get(Client, new_id).tenant_id == 1
        assert db.get(Client, new_id).created_by == 1
        db.rollback()
        assert db.get(Client, new_id) is None


def test_client_service_assignment_lifecycle_and_source_validation(crm):
    from app.services.clients import ClientService, RecordNotFound
    from app.services.principal import Principal
    from app.schemas.clients import ClientCreateSchema, ClientUpdateSchema
    _, factory, _ = crm
    with factory() as db:
        service = ClientService(db, Principal(3, 1, frozenset()))
        with pytest.raises(RecordNotFound):
            service.detail(1)
        with pytest.raises(RecordNotFound):
            service.create(ClientCreateSchema(name='forbidden', source_lead_id=1))
        with pytest.raises(RecordNotFound):
            service.create(ClientCreateSchema(name='foreign', source_lead_id=2))
        db.get(Client, 1).assigned_to = 3
        db.get(Lead, 1).assigned_to = 3
        db.flush()
        assert service.detail(1)['id'] == 1
        assert service.update(1, ClientUpdateSchema(type='Custom', phone='123-456-7890')) == 1
        assert service.delete(1) is True
        assert service.delete(1) is False
        with pytest.raises(RecordNotFound):
            service.detail(1)
        service.restore(1)
        assert service.detail(1)['id'] == 1
        new_id = service.create(ClientCreateSchema(name='converted', source_lead_id=1))
        assert service.detail(new_id)['lead_origin']['lead_id'] == 1
        db.commit()


def test_client_rest_lifecycle_and_read_audit(crm):
    import json
    from app.models import ActivityLog
    call, factory, _ = crm
    status, data = call('POST', '/api/clients', {'name': 'Lifecycle', 'tenant_id': 2, 'created_by': 2}, user=3)
    assert status == 201
    client_id = json.loads(data)['id']
    path = f'/api/clients/{client_id}'
    assert call('PUT', path, {'type': 'Custom', 'name': 'Updated'}, user=3)[0] == 200
    assert call('PUT', path, {'name': None}, user=3)[0] == 400
    status, data = call('GET', path, user=3)
    assert status == 200 and json.loads(data)['name'] == 'Updated'
    with factory() as db:
        client = db.get(Client, client_id)
        assert (client.tenant_id, client.created_by, client.updated_by) == (1, 3, 3)
        assert db.query(ActivityLog).filter_by(entity_id=client_id, user_id=3, tenant_id=1).count() == 1
    assert call('DELETE', path, user=3)[0] == 200
    assert call('GET', path, user=3)[0] == 404
    assert call('PUT', path + '/restore', user=3)[0] == 200
    assert call('GET', path, user=3)[0] == 200
