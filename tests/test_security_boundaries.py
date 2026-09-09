import asyncio
import importlib
import time
import os
import uuid
from datetime import datetime

import pytest
from authlib.jose import jwt
from quart import Quart
from sqlalchemy import create_engine, text, event
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Tenant, Role, User, Client, Lead, Project, Contact, Account, Interaction
from app.routes import register_blueprints
from app.utils import auth_utils


@pytest.fixture
def crm(tmp_path, monkeypatch, request):
    url = os.getenv('SECURITY_TEST_DATABASE_URL')
    schema = 'security_test_' + uuid.uuid4().hex
    admin_engine = create_engine(url, hide_parameters=True) if url else None
    engine = None

    def cleanup():
        try:
            if engine is not None:
                engine.dispose()
            if admin_engine is not None:
                # A fresh connection avoids reusing one broken during setup.
                admin_engine.dispose()
                with admin_engine.begin() as connection:
                    connection.execute(text("SET LOCAL lock_timeout='5s'"))
                    connection.execute(text("SET LOCAL statement_timeout='15s'"))
                    connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        finally:
            if admin_engine is not None:
                admin_engine.dispose()

    # Register before CREATE SCHEMA: setup failures also receive teardown.
    request.addfinalizer(cleanup)
    if admin_engine:
        with admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(url, hide_parameters=True, connect_args={'options': f'-csearch_path={schema}'})
    else:
        engine = create_engine(f"sqlite:///{tmp_path / 'security.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    for name in ('accounts', 'contacts', 'projects', 'interactions', 'clients', 'leads', 'auth', 'reports', 'imports', 'users', 'search'):
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
    runtime_role = os.getenv('SECURITY_TEST_ROLE')
    if runtime_role and admin_engine:
        from scripts.staging_database_role import grant_runtime_access
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE alembic_version (version_num varchar(32))"))
            grant_runtime_access(connection, runtime_role, schema)
            if os.getenv('CRM_RLS_ENABLED') == '1':
                from migrations.versions.tenant_rls_prepare import reconcile as prepare_rls
                from migrations.versions.tenant_row_security import reconcile as enable_rls
                prepare_rls(connection, schema, runtime_role)
                enable_rls(connection, schema)
        runtime_factory = sessionmaker(bind=engine)
        @event.listens_for(runtime_factory, 'after_begin')
        def set_runtime_role(session, transaction, connection):
            role_sql = connection.dialect.identifier_preparer.quote(runtime_role)
            connection.execute(text(f'SET LOCAL ROLE {role_sql}'))
        for name in ('accounts', 'contacts', 'projects', 'interactions', 'clients', 'leads', 'auth', 'reports', 'imports', 'users', 'search'):
            monkeypatch.setattr(importlib.import_module(f'app.routes.{name}'), 'SessionLocal', runtime_factory)
        monkeypatch.setattr(auth_utils, 'SessionLocal', runtime_factory)
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
    return call, factory, app


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
    for tenant in (1, 2):
        with factory() as db:
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
        with factory() as ordinary_db:
            assert SearchService(ordinary_db, Principal(3, 1, frozenset())).search('example.test') == []
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


@pytest.mark.parametrize('statement', [
    'SELECT * FROM backups LIMIT 0',
    'SELECT * FROM backup_restores LIMIT 0',
    'SELECT * FROM alembic_version LIMIT 0',
    'UPDATE tenants SET name=name WHERE false',
    'UPDATE roles SET name=name WHERE false',
    'TRUNCATE clients',
    'ALTER TABLE clients ADD COLUMN forbidden_probe integer',
    'CREATE TABLE forbidden_probe (id integer)',
])
def test_runtime_database_role_denies_platform_operations(crm, statement):
    from sqlalchemy.exc import DBAPIError
    runtime_role = os.getenv('SECURITY_TEST_ROLE')
    if not runtime_role or not os.getenv('SECURITY_TEST_DATABASE_URL'):
        pytest.skip('Requires PostgreSQL runtime-role validation')
    _, factory, _ = crm
    with factory() as db:
        quote = db.bind.dialect.identifier_preparer.quote
        db.execute(text(f'SET LOCAL ROLE {quote(runtime_role)}'))
        with pytest.raises(DBAPIError) as error:
            db.execute(text(statement))
        assert error.value.orig.pgcode == '42501'
        db.rollback()


def test_runtime_can_purge_empty_deleted_client(crm):
    import json
    call, factory, _ = crm
    status, body = call('POST', '/api/clients', {'name': 'Purge regression'})
    assert status == 201
    client_id = json.loads(body)['id']
    path = f'/api/clients/{client_id}'
    assert call('DELETE', path)[0] == 200
    assert call('DELETE', path + '/purge')[0] == 200
    with factory() as db:
        assert db.get(Client, client_id) is None


def test_request_logging_does_not_access_expired_orm_user(crm):
    from quart import request
    from app.services.principal import Principal
    from app.utils.logging_utils import get_request_context
    _, factory, _ = crm
    with factory() as db:
        user = db.get(User, 1)
        db.rollback()
    async def run():
        async with Quart(__name__).test_request_context('/probe'):
            request.user = user
            request.principal = Principal(1, 1, frozenset({'admin'}))
            context = get_request_context()
            assert context['user_id'] == 1
            assert context['tenant_id'] == 1
    asyncio.run(run())


def test_http_relationship_creation_with_composite_constraints(crm):
    import json
    from migrations.versions.tenant_relationships import reconcile
    call, factory, _ = crm
    if factory.kw['bind'].dialect.name != 'postgresql':
        pytest.skip('Requires PostgreSQL composite constraints')
    with factory.kw['bind'].begin() as connection:
        schema = connection.execute(text('SELECT current_schema()')).scalar_one()
        reconcile(connection, schema)
    status, body = call('POST', '/api/clients', {'name': 'Constrained client', 'source_lead_id': 1})
    assert status == 201
    client_id = json.loads(body)['id']
    for path, body in (
        ('/api/accounts', {'account_number': 'composite-test', 'client_id': client_id}),
        ('/api/contacts', {'first_name': 'Constrained', 'client_id': client_id}),
        ('/api/projects', {'project_name': 'Constrained', 'project_status': 'pending', 'client_id': client_id}),
        ('/api/interactions', {'summary': 'Constrained', 'client_id': client_id, 'contact_date': '2026-09-06T12:00:00'}),
    ):
        assert call('POST', path, body)[0] == 201
    assert call('GET', f'/api/clients/{client_id}')[0] == 200
    # Source lead is creation-only; update schema ignores attempts to change it.
    assert call('PUT', f'/api/clients/{client_id}', {'source_lead_id': 2})[0] == 200
    with factory() as db:
        assert db.get(Client, client_id).source_lead_id == 1
    assert call('POST', '/api/clients', {'name': 'Forbidden', 'source_lead_id': 2})[0] == 404


@pytest.fixture
def rls_runtime(crm):
    if os.getenv('CRM_RLS_ENABLED') != '1' or not os.getenv('SECURITY_TEST_ROLE') or not os.getenv('SECURITY_TEST_DATABASE_URL'):
        pytest.skip('Requires PostgreSQL row security and restricted runtime role')
    _, admin, _ = crm
    with admin() as db:
        schema = db.execute(text('SELECT current_schema()')).scalar_one()
        from app.models import UserPreference
        db.add_all([UserPreference(user_id=n, category='test', preference_key='key', preference_value={'owner': n}) for n in (1, 2)])
        db.commit()
    pool = create_engine(os.environ['SECURITY_TEST_DATABASE_URL'],
                         connect_args={'options': f'-csearch_path={schema}'},
                         pool_size=1, max_overflow=0, hide_parameters=True)
    factory = sessionmaker(bind=pool)
    @event.listens_for(factory, 'after_begin')
    def role_and_context(session, transaction, connection):
        from app.services.database_context import apply_context
        quoted = connection.dialect.identifier_preparer.quote(os.environ['SECURITY_TEST_ROLE'])
        connection.execute(text(f'SET LOCAL ROLE {quoted}'))
        apply_context(session, connection)
    try:
        yield factory, admin, schema
    finally:
        pool.dispose()


def test_rls_raw_sql_missing_context_and_pool_reuse(rls_runtime):
    from app.services.database_context import bind_principal
    from app.services.principal import Principal
    factory, _, _ = rls_runtime
    pids = set()
    for tenant, action in ((None, 'commit'), (1, 'commit'), (2, 'rollback'), (None, 'rollback'), (1, 'rollback')):
        with factory() as db:
            if tenant:
                bind_principal(db, Principal(tenant, tenant, frozenset({'admin'})))
            pids.add(db.execute(text('SELECT pg_backend_pid()')).scalar_one())
            expected = [] if tenant is None else [tenant]
            for table in ('clients', 'leads', 'projects'):
                assert db.execute(text(f'SELECT id FROM {table} ORDER BY id')).scalars().all() == expected
            assert db.execute(text('SELECT user_id FROM user_preferences ORDER BY user_id')).scalars().all() == expected
            assert db.execute(text('SELECT user_id FROM user_roles ORDER BY user_id')).scalars().all() == expected
            assert db.execute(text('SELECT id FROM tenants ORDER BY id')).scalars().all() == expected
            getattr(db, action)()
    assert len(pids) == 1  # Same physical PostgreSQL connection across all identities.


def test_rls_raw_joins_writes_and_bypass_attempts(rls_runtime):
    from app.services.database_context import bind_principal
    from app.services.principal import Principal
    from sqlalchemy.exc import DBAPIError
    factory, admin, _ = rls_runtime
    with factory() as db:
        bind_principal(db, Principal(1, 1, frozenset({'admin'})))
        assert db.execute(text('SELECT c.id, u.tenant_id FROM clients c JOIN users u ON c.created_by=u.id')).all() == [(1, 1)]
        for statement in (
            "INSERT INTO clients (id,tenant_id,created_by,name) VALUES (99,2,2,'blocked')",
            'UPDATE clients SET tenant_id=2 WHERE id=1',
        ):
            with db.begin_nested() as sp:
                with pytest.raises(DBAPIError) as exc:
                    db.execute(text(statement))
                assert exc.value.orig.pgcode == '42501'
                sp.rollback()
        assert db.execute(text("UPDATE clients SET name='Allowed' RETURNING id")).scalars().all() == [1]
        assert db.execute(text('DELETE FROM clients WHERE id=2')).rowcount == 0
        with db.begin_nested() as sp:
            db.execute(text('SET LOCAL row_security=off'))
            with pytest.raises(DBAPIError):
                db.execute(text('SELECT * FROM clients'))
            sp.rollback()
        db.rollback()
    with admin() as db:
        assert db.get(Client, 2).name == 'Private client 2'


def test_rls_bootstrap_is_identity_only_and_reset_is_narrow(rls_runtime):
    from app.services.database_context import auth_lookup
    factory, _, _ = rls_runtime
    for lookup in ({'user_id': 2}, {'email': 'b@example.test'}, {'email': 'missing@example.test'}):
        with factory() as db:
            auth_lookup(db, **lookup)
            expected = [] if lookup.get('email') == 'missing@example.test' else [2]
            assert db.execute(text('SELECT id FROM users')).scalars().all() == expected
            assert db.execute(text('SELECT id FROM tenants')).scalars().all() == expected
            assert db.execute(text('SELECT user_id FROM user_roles')).scalars().all() == expected
            assert db.execute(text('SELECT id FROM clients')).scalars().all() == []
            assert db.execute(text('SELECT id FROM user_preferences')).scalars().all() == []
            assert db.execute(text("UPDATE users SET password_hash='unchanged'")).rowcount == 0
    with factory() as db:
        auth_lookup(db, email='b@example.test', password_reset=True)
        assert db.execute(text("UPDATE users SET password_hash='reset-test' RETURNING id")).scalars().all() == [2]
        assert db.execute(text('SELECT id FROM clients')).scalars().all() == []
        db.rollback()


def test_rls_identity_cannot_change_within_session(rls_runtime):
    from app.services.database_context import bind_principal
    from app.services.principal import Principal
    factory, _, _ = rls_runtime
    with factory() as db:
        bind_principal(db, Principal(1, 1, frozenset()))
        assert db.get(Client, 1).id == 1
        db.commit()
        with pytest.raises(ValueError, match='cannot change'):
            bind_principal(db, Principal(2, 2, frozenset()))


def test_rls_all_tables_forced_and_bootstrap_function_hardened(rls_runtime):
    from migrations.versions.tenant_rls_prepare import TABLES
    _, admin, schema = rls_runtime
    with admin() as db:
        rows = db.execute(text('SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=:schema AND relkind=\'r\''), {'schema': schema}).all()
        assert {name for name, enabled, forced in rows if enabled and forced} == set(TABLES)
        function = db.execute(text("SELECT p.prosecdef,p.proconfig,pg_get_function_result(p.oid) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=:schema AND p.proname='crm_auth_identity'"), {'schema': schema}).one()
        assert function.prosecdef and function.proconfig == ['search_path=pg_catalog']
        assert function[2] == 'TABLE(user_id integer, tenant_id integer)'


def test_rls_concurrent_request_contexts_do_not_mix(rls_runtime):
    from quart import request
    from app.services.principal import Principal
    factory, _, _ = rls_runtime
    app = Quart(__name__)
    async def task(tenant):
        async with app.test_request_context('/probe'):
            request.principal = Principal(tenant, tenant, frozenset())
            for _ in range(3):
                await asyncio.sleep(0)
                with factory() as db:
                    assert db.execute(text('SELECT id FROM clients')).scalars().all() == [tenant]
    async def run():
        await asyncio.gather(task(1), task(2))
    asyncio.run(run())


def test_real_login_and_reset_with_row_security(crm, monkeypatch):
    from unittest.mock import AsyncMock
    from app.routes import auth as auth_routes
    call, factory, app = crm
    with factory() as db:
        db.get(User, 1).password_hash = auth_utils.hash_password('OriginalTestPassword!')
        db.commit()
    assert call('POST', '/api/login', {'email': 'a@example.test', 'password': 'OriginalTestPassword!'}, user=None)[0] == 200
    assert call('POST', '/api/login', {'email': 'a@example.test', 'password': 'WrongTestPassword!'}, user=None)[0] == 401
    delivery = AsyncMock()
    monkeypatch.setattr(auth_routes, 'send_password_reset_email', delivery)
    assert call('POST', '/api/forgot-password', {'email': 'a@example.test'}, user=None)[0] == 200
    delivery.assert_awaited_once()
    async def reset_token():
        async with app.app_context():
            return auth_utils.generate_reset_token('a@example.test')
    token = asyncio.run(reset_token())
    assert call('POST', '/api/reset-password', {'token': token, 'password': 'ChangedTestPassword!'}, user=None)[0] == 200
    assert call('POST', '/api/login', {'email': 'a@example.test', 'password': 'ChangedTestPassword!'}, user=None)[0] == 200


def _parent_rules(admin):
    from migrations.versions.parent_link_rules import reconcile
    if admin.kw['bind'].dialect.name != 'postgresql':
        pytest.skip('Requires PostgreSQL parent constraints')
    with admin.kw['bind'].begin() as c:
        schema = c.execute(text('SELECT current_schema()')).scalar_one()
        reconcile(c, schema)


def test_parent_rules_cover_every_combination_and_atomic_transfer(rls_runtime):
    from itertools import product
    from sqlalchemy.exc import DBAPIError
    from app.services.database_context import bind_principal
    from app.services.principal import Principal
    factory, admin, _ = rls_runtime
    _parent_rules(admin)
    cases = (
        ('contacts', ('client_id', 'lead_id'), '', '', True),
        ('interactions', ('client_id', 'lead_id', 'project_id'), ', followup_status', ", 'pending'", True),
        ('projects', ('client_id', 'lead_id'), ', project_name, project_status, created_by', ", 'Valid project', 'pending', 1", False),
    )
    with factory() as db:
        bind_principal(db, Principal(1, 1, frozenset({'admin'})))
        for table, fields, columns, values, required in cases:
            for parents in product((0, 1), repeat=len(fields)):
                legal = sum(parents) == 1 if required else sum(parents) <= 1
                literals = ','.join('1' if value else 'NULL' for value in parents)
                sql = f'INSERT INTO {table} (id,tenant_id,{",".join(fields)}{columns}) VALUES (99,1,{literals}{values})'
                with db.begin_nested() as sp:
                    if legal:
                        assert db.execute(text(sql)).rowcount == 1
                    else:
                        with pytest.raises(DBAPIError) as exc:
                            db.execute(text(sql))
                        assert exc.value.orig.pgcode == '23514'
                    sp.rollback()
        with db.begin_nested() as sp:
            with pytest.raises(DBAPIError) as exc:
                db.execute(text('UPDATE contacts SET client_id=NULL WHERE id=1'))
            assert exc.value.orig.pgcode == '23514'
            sp.rollback()
        assert db.execute(text('UPDATE contacts SET client_id=NULL, lead_id=1 WHERE id=1')).rowcount == 1
        db.rollback()


def test_parent_rule_preflight_keeps_invalid_rows_and_history(crm):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect
    _, admin, _ = crm
    if admin.kw['bind'].dialect.name != 'postgresql':
        pytest.skip('Requires PostgreSQL migration')
    with admin() as db:
        schema = db.execute(text('SELECT current_schema()')).scalar_one()
        db.add(Contact(id=99, tenant_id=1, first_name='Legacy orphan'))
        db.execute(text('CREATE TABLE IF NOT EXISTS alembic_version (version_num varchar(32) PRIMARY KEY)'))
        db.execute(text("INSERT INTO alembic_version VALUES ('tenant_row_security')"))
        db.commit()
    with pytest.raises(RuntimeError, match='invalid parent combinations'):
        with admin.kw['bind'].begin() as c:
            cfg = Config('alembic.ini')
            cfg.attributes.update(connection=c, version_table_schema=schema)
            command.upgrade(cfg, 'parent_link_rules')
    with admin() as db:
        assert db.get(Contact, 99).client_id is None
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == 'tenant_row_security'
        assert not any(x['name'] == 'ck_contacts_one_parent' for x in inspect(db.connection()).get_check_constraints('contacts', schema=schema))


def test_parent_rule_ddl_failure_rolls_back_prior_constraints(crm):
    from migrations.versions.parent_link_rules import reconcile
    from sqlalchemy import inspect
    from sqlalchemy.exc import DBAPIError
    _, admin, _ = crm
    if admin.kw['bind'].dialect.name != 'postgresql':
        pytest.skip('Requires PostgreSQL migration')
    with admin.kw['bind'].begin() as c:
        schema = c.execute(text('SELECT current_schema()')).scalar_one()
        c.execute(text('ALTER TABLE projects ADD CONSTRAINT ck_projects_at_most_one_parent CHECK (true)'))
    with pytest.raises(DBAPIError):
        with admin.kw['bind'].begin() as c:
            reconcile(c, schema)
    with admin.kw['bind'].connect() as c:
        assert not any(x['name'] == 'ck_contacts_one_parent' for x in inspect(c).get_check_constraints('contacts', schema=schema))


@pytest.mark.parametrize('resource,model,parent_field,ids_field', [
    ('clients', Client, 'client_id', 'client_ids'),
    ('leads', Lead, 'lead_id', 'lead_ids'),
])
def test_parent_rules_purge_conflicts_preserve_single_and_bulk_records(crm, resource, model, parent_field, ids_field):
    import json
    call, admin, _ = crm
    _parent_rules(admin)
    ids = []
    for name in ('Parent with child', 'Parent without child'):
        status, body = call('POST', f'/api/{resource}', {'name': name})
        assert status == 201
        ids.append(json.loads(body)['id'])
    status, body = call('POST', '/api/contacts', {'first_name': 'Child', parent_field: ids[0]})
    assert status == 201
    contact_id = json.loads(body)['id']
    for client_id in ids:
        assert call('DELETE', f'/api/{resource}/{client_id}')[0] == 200
    assert call('DELETE', f'/api/{resource}/{ids[0]}/purge')[0] == 409
    assert call('POST', f'/api/{resource}/bulk-purge', {ids_field: ids})[0] == 409
    with admin() as db:
        assert all(db.get(model, client_id) is not None for client_id in ids)
        assert getattr(db.get(Contact, contact_id), parent_field) == ids[0]
    assert call('PUT', f'/api/{resource}/{ids[0]}/restore')[0] == 200
    assert call('DELETE', f'/api/contacts/{contact_id}')[0] == 200
    assert call('DELETE', f'/api/{resource}/{ids[0]}')[0] == 200
    assert call('DELETE', f'/api/{resource}/{ids[0]}/purge')[0] == 200
    assert call('POST', f'/api/{resource}/bulk-purge', {ids_field: [ids[1]]})[0] == 200


@pytest.mark.parametrize('operation', ['detail', 'update', 'delete', 'restore'])
def test_lead_service_denies_other_tenant_without_http(crm, operation):
    from app.services.leads import LeadService, RecordNotFound
    from app.services.principal import Principal
    from app.schemas.leads import LeadUpdateSchema
    _, factory, _ = crm
    with factory() as db:
        service = LeadService(db, Principal(1, 1, frozenset({'admin'})))
        with pytest.raises(RecordNotFound):
            if operation == 'update':
                service.update(2, LeadUpdateSchema(name='leak'))
            else:
                getattr(service, operation)(2)
        db.commit()
    with factory() as db:
        assert db.get(Lead, 2).name == 'Private lead 2'
        assert db.get(Lead, 2).deleted_at is None


def test_lead_service_ownership_pure_detail_and_rollback(crm):
    from app.services.leads import LeadService, RecordNotFound
    from app.services.principal import Principal
    from app.schemas.leads import LeadCreateSchema, LeadUpdateSchema
    from app.models import ActivityLog
    _, factory, _ = crm
    with factory() as db:
        db.add_all([Contact(tenant_id=1, lead_id=1, first_name='Allowed', last_name='Contact'),
                    Contact(tenant_id=2, lead_id=1, first_name='Foreign'),
                    Contact(tenant_id=1, lead_id=1, client_id=1, first_name='Malformed')])
        db.commit()
    with factory() as db:
        service = LeadService(db, Principal(3, 1, frozenset()))
        with pytest.raises(RecordNotFound):
            service.detail(1)
        db.get(Lead, 1).assigned_to = 3
        db.flush()
        assert [c['name'] for c in service.detail(1)['contacts']] == ['Allowed Contact']
        assert db.query(ActivityLog).count() == 0
        service.update(1, LeadUpdateSchema(lead_status='won', phone='123-456-7890'))
        converted = db.get(Lead, 1).converted_on
        assert converted is not None
        service.update(1, LeadUpdateSchema(lead_status='won'))
        assert db.get(Lead, 1).converted_on == converted
        assert service.delete(1) is True
        assert service.delete(1) is False
        with pytest.raises(RecordNotFound):
            service.detail(1)
        service.restore(1)
        assert service.detail(1)['id'] == 1
        db.rollback()
        assert db.get(Lead, 1).converted_on is None
        assert db.get(Lead, 1).deleted_at is None
        new_id = service.create(LeadCreateSchema(name='Rollback', tenant_id=2, created_by=2))
        assert (db.get(Lead, new_id).tenant_id, db.get(Lead, new_id).created_by) == (1, 3)
        db.rollback()
        assert db.get(Lead, new_id) is None


def test_lead_rest_service_lifecycle_and_view_audit(crm):
    import json
    from app.models import ActivityLog
    call, factory, _ = crm
    status, body = call('POST', '/api/leads', {'name': 'Lifecycle', 'tenant_id': 2, 'created_by': 2}, user=3)
    assert status == 201
    lead_id = json.loads(body)['id']
    path = f'/api/leads/{lead_id}'
    assert call('PUT', path, {'name': 'Updated', 'lead_status': 'won'}, user=3)[0] == 200
    assert call('PUT', path, {'name': None}, user=3)[0] == 400
    assert call('PUT', path, [], user=3)[0] == 400
    status, body = call('GET', path, user=3)
    assert status == 200 and json.loads(body)['name'] == 'Updated'
    with factory() as db:
        lead = db.get(Lead, lead_id)
        assert (lead.tenant_id, lead.created_by, lead.updated_by) == (1, 3, 3)
        assert lead.converted_on is not None
        assert db.query(ActivityLog).filter_by(entity_type='lead', entity_id=lead_id, user_id=3, tenant_id=1).count() == 1
    assert call('GET', path, user=2)[0] == 404
    assert call('DELETE', path, user=3)[0] == 200
    assert call('GET', path, user=3)[0] == 404
    assert call('PUT', path + '/restore', user=2)[0] == 404
    assert call('PUT', path + '/restore', user=3)[0] == 200
    assert call('GET', path, user=3)[0] == 200


@pytest.mark.parametrize('method,args', [('list_all', ()), ('list_assigned', ()),
    ('bulk_delete', ([1, 2],)), ('bulk_purge', ([1, 2],)), ('purge', (1,))])
def test_lead_admin_services_reject_ordinary_principal(crm, method, args):
    from app.services.leads import LeadService
    from app.services.principal import Principal
    _, factory, _ = crm
    with factory() as db:
        service = LeadService(db, Principal(3, 1, frozenset()))
        with pytest.raises(PermissionError):
            getattr(service, method)(*args)


def test_lead_lists_preserve_personal_admin_and_trash_boundaries(crm):
    from app.services.leads import LeadService
    from app.services.principal import Principal
    _, factory, _ = crm
    with factory() as db:
        db.get(Lead, 1).assigned_to = 3
        db.commit()
    with factory() as db:
        admin = LeadService(db, Principal(1, 1, frozenset({'admin'})))
        assert admin.list_mine()['total'] == 0
        assert [r['id'] for r in admin.list_all()['leads']] == [1]
        assert [r['id'] for r in admin.list_assigned()] == [1]
        assert admin.list_all(user_email='b@example.test')['total'] == 0
        assert admin.list_all(user_email='ordinary@example.test')['total'] == 1
        assert admin.bulk_delete([1, 2]) == 1
        assert [r['id'] for r in admin.list_trash()] == [1]
        assert admin.bulk_purge([1, 2]) == 1
        db.rollback()
        assert db.get(Lead, 1) is not None and db.get(Lead, 1).deleted_at is None
        assert db.get(Lead, 2).deleted_at is None
    with factory() as db:
        ordinary = LeadService(db, Principal(3, 1, frozenset()))
        assert [r['id'] for r in ordinary.list_mine()['leads']] == [1]
        ordinary.delete(1)
        assert [r['id'] for r in ordinary.list_trash()] == [1]
        db.rollback()


def test_lead_list_bulk_http_validation_and_contract(crm):
    import json
    call, _, _ = crm
    for path in ['/api/leads?page=0', '/api/leads/all?per_page=0', '/api/leads?page=oops']:
        assert call('GET', path)[0] == 400
    for path in ['/api/leads/bulk-delete', '/api/leads/bulk-purge']:
        for body in ([], {'lead_ids': [True]}, {'lead_ids': ['1']}, {'lead_ids': []}):
            assert call('POST', path, body)[0] == 400
        assert call('POST', path, {'lead_ids': [1]}, user=3)[0] == 403
    status, body = call('GET', '/api/leads/all?sort=alphabetical&per_page=1')
    result = json.loads(body)
    assert status == 200 and result['total'] == 1 and result['leads'][0]['id'] == 1
    assert result['leads'][0]['created_by_name'] == 'a@example.test'
    assert call('POST', '/api/leads/bulk-delete', {'lead_ids': [1, 2]})[0] == 200
    assert [r['id'] for r in json.loads(call('GET', '/api/leads/trash')[1])] == [1]
    assert call('GET', '/api/leads/2', user=2)[0] == 200
    assert call('POST', '/api/leads/bulk-purge', {'lead_ids': [1, 2]})[0] == 200



def test_lead_assignment_service_authorization_and_rollback(crm):
    from app.services.leads import LeadService, RecordNotFound
    from app.services.principal import Principal
    from app.schemas.leads import LeadAssignSchema
    _, factory, _ = crm
    with factory() as db:
        ordinary = LeadService(db, Principal(3, 1, frozenset()))
        with pytest.raises(PermissionError):
            ordinary.assign(1, LeadAssignSchema(assigned_to=3))
    with factory() as db:
        service = LeadService(db, Principal(1, 1, frozenset({'admin'})))
        with pytest.raises(RecordNotFound):
            service.assign(2, LeadAssignSchema(assigned_to=1))
        with pytest.raises(ValueError):
            service.assign(1, LeadAssignSchema(assigned_to=2))
        db.get(User, 3).is_active = False
        db.flush()
        with pytest.raises(ValueError):
            service.assign(1, LeadAssignSchema(assigned_to=3))
        db.rollback()
        notice = service.assign(1, LeadAssignSchema(assigned_to=3))
        assert notice['to_email'] == 'ordinary@example.test'
        assert db.get(Lead, 1).assigned_to == 3
        assert db.get(Lead, 1).updated_by == 1
        db.rollback()
        assert db.get(Lead, 1).assigned_to is None
        service.delete(1)
        with pytest.raises(RecordNotFound):
            service.assign(1, LeadAssignSchema(assigned_to=3))


def test_lead_assignment_http_notifies_only_after_commit(crm, monkeypatch):
    from app.routes import leads
    call, factory, _ = crm
    notices = []
    async def notification(**kwargs):
        with factory() as db:
            assert db.get(Lead, 1).assigned_to == 3
        notices.append(kwargs)
        raise RuntimeError('Synthetic mail failure')
    monkeypatch.setattr(leads, 'send_assignment_notification', notification)
    for body in ([], {'assigned_to': True}, {'assigned_to': '3'}, {'assigned_to': 0}):
        assert call('PUT', '/api/leads/1/assign', body)[0] == 400
    assert call('PUT', '/api/leads/1/assign', {'assigned_to': 3}, user=3)[0] == 403
    assert call('PUT', '/api/leads/2/assign', {'assigned_to': 3})[0] == 404
    assert call('PUT', '/api/leads/1/assign', {'assigned_to': 2})[0] == 400
    assert notices == []
    assert call('PUT', '/api/leads/1/assign', {'assigned_to': 3})[0] == 200
    assert len(notices) == 1 and notices[0]['assigned_by'] == 'a@example.test'
    with factory() as db:
        assert db.get(Lead, 1).assigned_to == 3


def test_lead_assignment_failed_commit_does_not_notify(crm, monkeypatch):
    from app.routes import leads
    from sqlalchemy.exc import SQLAlchemyError
    call, factory, _ = crm
    route_factory = leads.SessionLocal
    notices = []
    async def notification(**kwargs):
        notices.append(kwargs)
    def failing_session():
        session = route_factory()
        def fail():
            raise SQLAlchemyError('Synthetic private database detail')
        session.commit = fail
        return session
    monkeypatch.setattr(leads, 'SessionLocal', failing_session)
    monkeypatch.setattr(leads, 'send_assignment_notification', notification)
    status, body = call('PUT', '/api/leads/1/assign', {'assigned_to': 3})
    assert status == 500 and 'Synthetic private' not in body
    assert notices == []
    with factory() as db:
        assert db.get(Lead, 1).assigned_to is None


@pytest.mark.parametrize('user', [1, 3])
def test_contact_service_enforces_current_and_destination_parent(crm, user):
    from app.services.contacts import ContactService, RecordNotFound
    from app.services.principal import Principal
    from app.schemas.contacts import ContactCreateSchema, ContactUpdateSchema
    _, factory, _ = crm
    with factory() as db:
        service = ContactService(db, Principal(user, 1, frozenset({'admin'}) if user == 1 else frozenset()))
        with pytest.raises(RecordNotFound):
            service.create(ContactCreateSchema(first_name='Foreign', client_id=2))
        with pytest.raises(RecordNotFound):
            service.list_for_parent(lead_id=2)
        if user == 3:
            for action in (lambda: service.list_for_parent(client_id=1),
                           lambda: service.update(1, ContactUpdateSchema(client_id=None, lead_id=1)),
                           lambda: service.delete(1)):
                with pytest.raises(RecordNotFound):
                    action()
        else:
            with pytest.raises(RecordNotFound):
                service.update(1, ContactUpdateSchema(client_id=None, lead_id=2))
        assert db.get(Contact, 1).client_id == 1


def test_contact_service_atomic_transfer_pure_list_and_rollback(crm):
    from app.services.contacts import ContactService
    from app.services.principal import Principal
    from app.schemas.contacts import ContactCreateSchema, ContactUpdateSchema
    from app.models import ActivityLog
    _, factory, _ = crm
    with factory() as db:
        service = ContactService(db, Principal(1, 1, frozenset({'admin'})))
        assert service.list_for_parent() == []
        assert len(service.list_for_parent(client_id=1)) == 1
        assert db.query(ActivityLog).count() == 0
        with pytest.raises(ValueError):
            service.update(1, ContactUpdateSchema(client_id=None))
        with pytest.raises(ValueError):
            service.update(1, ContactUpdateSchema(lead_id=1))
        service.update(1, ContactUpdateSchema(client_id=None, lead_id=1, phone='123-456-7890'))
        assert len(service.list_for_parent(lead_id=1)) == 1
        service.delete(1)
        db.rollback()
        assert db.get(Contact, 1).client_id == 1
        new_id = service.create(ContactCreateSchema(first_name='Rollback', lead_id=1, tenant_id=2))
        assert db.get(Contact, new_id).tenant_id == 1
        db.rollback()
        assert db.get(Contact, new_id) is None


def test_contact_service_denies_deleted_parent_and_foreign_contact(crm):
    from app.services.contacts import ContactService, RecordNotFound
    from app.services.principal import Principal
    from app.schemas.contacts import ContactUpdateSchema
    _, factory, _ = crm
    with factory() as db:
        foreign = Contact(tenant_id=2, lead_id=2, first_name='Foreign')
        db.add(foreign); db.flush(); foreign_id = foreign.id
        db.get(Client, 1).deleted_at = datetime.utcnow()
        db.commit()
    with factory() as db:
        service = ContactService(db, Principal(1, 1, frozenset({'admin'})))
        for target in (1, foreign_id):
            with pytest.raises(RecordNotFound):
                service.update(target, ContactUpdateSchema(notes='Denied'))
            with pytest.raises(RecordNotFound):
                service.delete(target)
