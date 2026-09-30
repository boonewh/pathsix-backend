"""Fault injection and complete table inventory, in disposable schemas only."""
import asyncio
from datetime import datetime

import pytest
from quart import request
from sqlalchemy import text, true
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from test_security_boundaries import crm, rls_runtime
from app.database import Base, IdentitySession
from app.models import (User,Tenant,Client,Lead,Project,Account,Contact,File,Subscription,ActivityLog,
                        ActivityType,ChatMessage,UserPreference)
from app.services.base import TenantService
from app.services.clients import ClientService
from app.services.users import UserService
from app.services.database_context import auth_lookup, bind_principal
from app.services.principal import Principal
from app.services.errors import RecordNotFound
from migrations.versions.tenant_rls_prepare import BUSINESS
from ops.isolation_contract import TABLES, verify_isolation_contract, expected_policies

A=Principal(1,1,frozenset({'admin'}))
B=Principal(2,2,frozenset({'admin'}))


def test_every_model_has_an_explicit_isolation_classification():
    # Adding a model requires choosing and proving its isolation boundary.
    assert set(Base.metadata.tables)==set(TABLES)|{'roles','backups','backup_restores','ai_clients'}
    for table in BUSINESS+('users',):
        assert 'tenant_id' in Base.metadata.tables[table].columns


@pytest.mark.parametrize('kwargs', [{'user_id':True},{'tenant_id':0},{'user_id':'1'},
    {'roles':'admin'},{'roles':[None]},{'connection_type':'mcp'},{'connection_type':'worker'}])
def test_malformed_or_unimplemented_principal_boundaries_are_rejected(kwargs):
    values={'user_id':1,'tenant_id':1,'roles':set()};values.update(kwargs)
    with pytest.raises(ValueError): Principal(**values)


def test_principal_copies_mutable_role_inputs():
    roles={'admin'};principal=Principal(1,1,roles);roles.clear()
    assert principal.roles==frozenset({'admin'})


@pytest.mark.parametrize('finish', ['commit','rollback','close'])
def test_authentication_lookup_cannot_change_or_be_promoted(crm, finish):
    with crm[1]() as db:
        auth_lookup(db,user_id=1)
        db.execute(text('SELECT 1'))
        getattr(db,finish)()
        with pytest.raises(ValueError): auth_lookup(db,user_id=2)
        with pytest.raises(ValueError): auth_lookup(db,user_id=1,password_reset=True)
        with pytest.raises(ValueError): bind_principal(db,A)
    with crm[1]() as db:
        bind_principal(db,A)
        with pytest.raises(ValueError): auth_lookup(db,user_id=1)
    with crm[1]() as db:
        db.execute(text('SELECT 1'))
        with pytest.raises(ValueError): auth_lookup(db,user_id=1)


@pytest.mark.parametrize('kind', ['user','tenant','preference'])
def test_foreign_identity_map_cannot_be_adopted(crm, kind):
    factory=crm[1]
    with factory() as db:
        db.add(UserPreference(user_id=2,category='test',preference_key='private',preference_value={}))
        db.commit()
    with factory() as db:
        cached=(db.get(User,2) if kind=='user' else db.get(Tenant,2) if kind=='tenant'
                else db.query(UserPreference).filter_by(user_id=2).one())
        assert cached is not None
        with pytest.raises(ValueError,match='foreign cached'): bind_principal(db,A)


@pytest.mark.parametrize('finish', ['commit','rollback','close'])
def test_http_session_reuse_rejects_even_cached_gets(crm, finish):
    factory=sessionmaker(bind=crm[1].kw['bind'],class_=IdentitySession,expire_on_commit=False)
    app=crm[2]
    async def exercise():
        with factory() as db:
            async with app.test_request_context('/a'):
                request.principal=A
                cached=db.get(User,1)
                assert cached.id==1
                getattr(db,finish)()
            async with app.test_request_context('/b'):
                request.principal=B
                with pytest.raises(ValueError,match='cannot change'): db.get(User,1)
                with pytest.raises(ValueError,match='cannot change'): db.execute(text('SELECT 1'))
    asyncio.run(exercise())


def test_direct_connection_initializes_implicit_http_identity(crm):
    app=crm[2]
    async def exercise():
        async with app.test_request_context('/probe'):
            request.principal=A
            with crm[1]() as db:
                db.connection()
                assert db.info['principal']==A
    asyncio.run(exercise())


@pytest.mark.parametrize('operation', ['flush','commit'])
def test_open_transaction_cannot_write_for_another_request(crm, operation):
    async def exercise():
        with crm[1]() as db:
            async with crm[2].test_request_context('/a'):
                request.principal=A
                row=db.get(Client,1)
            async with crm[2].test_request_context('/b'):
                request.principal=B
                row.name='Must not persist'
                with pytest.raises(ValueError,match='cannot change'): getattr(db,operation)()
            db.rollback()
    asyncio.run(exercise())


def test_authenticated_session_cannot_be_reused_by_an_anonymous_request(crm):
    factory=sessionmaker(bind=crm[1].kw['bind'],class_=IdentitySession)
    with factory() as db:
        bind_principal(db,A)
        async def exercise():
            async with crm[2].test_request_context('/anonymous'):
                with pytest.raises(ValueError,match='unauthenticated'): db.get(User,1)
                with pytest.raises(ValueError,match='unauthenticated'): db.execute(text('SELECT 1'))
        asyncio.run(exercise())


def assert_user_directory_is_scoped(factory):
    with factory() as db:
        assert {row['id'] for row in UserService(db,A).list()}=={1,3}


def test_cross_tenant_proof_fails_if_service_predicate_is_removed(crm, monkeypatch):
    # Deliberately use the operator fixture: RLS must not mask this layer's failure.
    assert_user_directory_is_scoped(crm[1])
    monkeypatch.setattr(TenantService,'_query',lambda service,*entities:service.session.query(*entities))
    with pytest.raises(AssertionError): assert_user_directory_is_scoped(crm[1])


def test_record_access_proof_fails_if_ownership_predicate_is_removed(crm, monkeypatch):
    from app.services import clients
    def denied():
        with crm[1]() as db:
            try:
                ClientService(db,Principal(3,1,frozenset())).detail(1)
            except RecordNotFound:
                return
            raise AssertionError('Unauthorized same-tenant record became visible')
    denied()
    monkeypatch.setattr(clients,'owned_record_filter',lambda *args,**kwargs:true())
    with pytest.raises(AssertionError): denied()


@pytest.mark.parametrize('resource,model', [('clients',Client),('leads',Lead),('projects',Project)])
@pytest.mark.parametrize('user', [1,3])
def test_foreign_lifecycle_and_bulk_targets_match_absent_records(crm, resource, model, user):
    call,factory,_=crm
    for method,suffix,body in [('GET','',None),('PUT','',{'notes':'denied'}),
        ('DELETE','',None),('PUT','/restore',{}),('DELETE','/purge',None),
        ('PUT','/assign',{'assigned_to':1})]:
        foreign=call(method,f'/api/{resource}/2'+suffix,body,user=user)
        missing=call(method,f'/api/{resource}/99999'+suffix,body,user=user)
        assert foreign==missing and foreign[0] in (403,404)
    for suffix in ('bulk-delete','bulk-purge'):
        responses=[]
        for ident in (2,99999):
            status,body=call('POST',f'/api/{resource}/{suffix}',{resource[:-1]+'_ids':[ident]},user=user)
            assert status in (200,403,404) and 'Private' not in body
            responses.append((status,body))
        assert responses[0]==responses[1]
    with factory() as db:
        target=db.get(model,2)
        assert target is not None and target.deleted_at is None and target.assigned_to is None


def test_foreign_changes_do_not_affect_lists_search_reports_or_counts(crm):
    call,factory,_=crm
    paths=['/api/clients','/api/clients/all','/api/leads','/api/leads/all',
           '/api/projects','/api/projects/all','/api/accounts','/api/subscriptions',
           '/api/search?q=Private','/api/reports','/api/reports/pipeline',
           '/api/reports/conversion-rate','/api/reports/lead-source']
    before={path:call('GET',path) for path in paths}
    assert all(status==200 for status,_ in before.values())
    with factory() as db:
        db.add_all([Client(id=20,tenant_id=2,created_by=2,name='Private foreign addition'),
                    Lead(id=20,tenant_id=2,created_by=2,name='Private foreign addition'),
                    Project(id=20,tenant_id=2,created_by=2,project_name='Private foreign addition',project_status='won')])
        db.commit()
    assert {path:call('GET',path) for path in paths}==before


@pytest.fixture
def populated_runtime(rls_runtime):
    from app.models import AIClient, AIConnection, OAuthCredential
    from datetime import timedelta
    runtime,admin,schema=rls_runtime
    with admin() as db:
        db.add(AIClient(id='proof',name='Proof client',is_active=True,allowed_scopes=['clients:read']))
        db.flush()
        for tenant in (1,2):
            db.add(AIConnection(id=str(tenant),tenant_id=tenant,user_id=tenant,client_id='proof',
                                resource='https://example.test/mcp',scopes=['clients:read'],
                                expires_at=datetime.utcnow()+timedelta(days=1)))
        db.flush()
        for tenant in (1,2):
            db.add(OAuthCredential(token_hash=str(tenant)*64,tenant_id=tenant,user_id=tenant,connection_id=str(tenant),kind='access',scopes=['clients:read'],expires_at=datetime.utcnow()+timedelta(minutes=5)))
        db.add_all([Account(id=2,tenant_id=2,client_id=2,account_number='B'),
                    Contact(id=2,tenant_id=2,client_id=2,first_name='B')])
        for tenant in (1,2):
            db.add_all([
                File(id=tenant,tenant_id=tenant,user_id=tenant,filename=f'private{tenant}',
                     stored_name=f'private{tenant}',path=f'tenant-{tenant}/private',size=0,mimetype='text/plain'),
                Subscription(id=tenant,tenant_id=tenant,client_id=tenant,created_by=tenant,
                             plan_name=f'Private {tenant}',price=1,billing_cycle='monthly',start_date=datetime(2026,1,1)),
                ActivityLog(tenant_id=tenant,user_id=tenant,action=ActivityType.viewed,
                            entity_type='client',entity_id=tenant,description=f'Private {tenant}'),
                ChatMessage(tenant_id=tenant,sender_id=tenant,content=f'Private {tenant}')])
        db.commit()
    return runtime,admin,schema


@pytest.mark.parametrize('table', TABLES)
def test_all_tables_deny_missing_context_and_foreign_reads_writes(populated_runtime, table):
    runtime,_,_=populated_runtime
    # Chat is an intentionally unavailable feature: no table privileges at all.
    if table=='chat_messages':
        with runtime() as db:
            bind_principal(db,A)
            with pytest.raises(DBAPIError) as error: db.execute(text('SELECT * FROM chat_messages'))
            assert error.value.orig.pgcode=='42501'
        return
    column='id' if table=='tenants' else 'user_id' if table in ('user_roles','user_preferences') else 'tenant_id'
    with runtime() as db:
        assert db.execute(text(f'SELECT {column} FROM {table}')).all()==[]
    with runtime() as db:
        bind_principal(db,A)
        values=db.execute(text(f'SELECT {column} FROM {table}')).scalars().all()
        assert values and set(values)=={1}
        if table=='tenants':
            with pytest.raises(DBAPIError) as error: db.execute(text('UPDATE tenants SET name=name'))
            assert error.value.orig.pgcode=='42501'
        else:
            assert db.execute(text(f'UPDATE {table} SET {column}={column} WHERE {column}=2')).rowcount==0
            if table not in ('ai_connections','oauth_credentials'):  # Consent history has no runtime DELETE grant.
                assert db.execute(text(f'DELETE FROM {table} WHERE {column}=2')).rowcount==0
            with db.begin_nested() as savepoint:
                with pytest.raises(DBAPIError) as error:
                    db.execute(text(f'UPDATE {table} SET {column}=2 WHERE {column}=1'))
                assert error.value.orig.pgcode in ({'23514','42501'} if table in ('ai_connections','oauth_credentials') else {'42501'})
                savepoint.rollback()
        db.rollback()


@pytest.mark.parametrize('mutation', ['remove','weaken','extra','disable_force'])
def test_policy_attestation_detects_faults_on_every_protected_table(rls_runtime, mutation):
    import os
    _,admin,schema=rls_runtime
    with admin() as db:
        connection=db.connection()
        assert verify_isolation_contract(connection,schema,os.environ['SECURITY_TEST_ROLE'])['tables']==len(TABLES)
        quote=connection.dialect.identifier_preparer.quote
        for table in TABLES:
            key=next(key for key in expected_policies() if key[0]==table)
            qualified=f'{quote(schema)}.{quote(table)}';policy=quote(key[1])
            with db.begin_nested() as savepoint:
                if mutation=='remove': sql=f'DROP POLICY {policy} ON {qualified}'
                elif mutation=='extra': sql=f'CREATE POLICY bypass_probe ON {qualified} USING (true)'
                elif mutation=='disable_force': sql=f'ALTER TABLE {qualified} NO FORCE ROW LEVEL SECURITY'
                else:
                    clause='USING' if expected_policies()[key][0] is not None else 'WITH CHECK'
                    sql=f'ALTER POLICY {policy} ON {qualified} {clause} (true)'
                db.execute(text(sql))
                with pytest.raises(RuntimeError,match='drift'):
                    verify_isolation_contract(connection,schema,os.environ['SECURITY_TEST_ROLE'])
                savepoint.rollback()
            verify_isolation_contract(connection,schema,os.environ['SECURITY_TEST_ROLE'])


def test_raw_row_proof_fails_when_policy_is_weakened(rls_runtime):
    runtime,admin,schema=rls_runtime
    def scoped():
        with runtime() as db:
            bind_principal(db,A)
            assert db.execute(text('SELECT tenant_id FROM clients')).scalars().all()==[1]
    scoped()
    # This schema belongs exclusively to the test fixture and is always dropped.
    with admin() as db:
        db.execute(text(f'ALTER POLICY crm_all ON "{schema}".clients USING (true)'))
        db.commit()
    with pytest.raises(AssertionError): scoped()
