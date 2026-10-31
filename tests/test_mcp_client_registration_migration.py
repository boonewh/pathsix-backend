"""Rehearse the client registration migration, its function grants and rollback."""
import json
import os
from pathlib import Path
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text, inspect
from sqlalchemy.exc import DBAPIError
from test_mcp_lead_migration import legacy as lead_legacy, upgrade as lead_upgrade
from test_mcp_audit_migration import legacy as audit_legacy
from test_oauth_migration import consent_legacy, legacy as oauth_legacy
from migrations.versions.mcp_client_registration import revision, down_revision
from migrations.versions import mcp_client_registration


@pytest.fixture
def legacy(lead_legacy):
    engine,schema=lead_legacy
    lead_upgrade(engine,schema,os.environ['SECURITY_TEST_ROLE'])
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        c.execute(text("INSERT INTO ai_clients (id,name,is_active,allowed_scopes,redirect_uris,oauth_enabled) VALUES "
                       "('chatgpt','ChatGPT',true,'[\"clients:read\"]','[\"https://chat.example/cb\"]',true),"
                       "('offline','Offline',true,'[\"clients:read\"]','[]',false)"))
        # Production runtime roles can resolve objects in the application schema.
        c.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO "{os.environ["SECURITY_TEST_ROLE"]}"'))
    return engine,schema


def upgrade(engine,schema,role):
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        root=Path(mcp_client_registration.__file__).resolve().parents[2]
        cfg=Config(str(root/'alembic.ini'))
        cfg.set_main_option('script_location',str(root/'migrations'))
        cfg.attributes.update(connection=c,version_table_schema=schema,runtime_role=role)
        command.upgrade(cfg,revision)


def register(c,ident,kind='dcr',scopes=('clients:read',),redirects=('http://127.0.0.1/cb',)):
    c.execute(text('SELECT crm_register_ai_client(:id,:name,CAST(:r AS json),:kind,CAST(:s AS json))'),
              {'id':ident,'name':'App','r':json.dumps(list(redirects)),'kind':kind,'s':json.dumps(list(scopes))})


def test_migration_keeps_operator_trust_and_runtime_read_only(legacy):
    engine,schema=legacy;role=os.environ['SECURITY_TEST_ROLE']
    upgrade(engine,schema,role)
    with engine.connect() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        assert c.execute(text('SELECT version_num FROM alembic_version')).scalar_one()==revision
        rows=dict(c.execute(text('SELECT id,inline_review FROM ai_clients WHERE registration=\'operator\'')).all())
        assert rows=={'chatgpt':True,'offline':False}
        for privilege in ('INSERT','UPDATE','DELETE'):
            assert not c.execute(text('SELECT has_table_privilege(:role,:table,:privilege)'),
                {'role':role,'table':schema+'.ai_clients','privilege':privilege}).scalar_one()
        function=c.execute(text("SELECT prosecdef,proconfig FROM pg_proc WHERE oid='crm_register_ai_client(text,text,json,text,json)'::regprocedure")).one()
        assert function==(True,['search_path=pg_catalog'])
        assert c.execute(text("SELECT has_function_privilege(:role,'crm_register_ai_client(text,text,json,text,json)','EXECUTE')"),{'role':role}).scalar_one()
        assert not c.execute(text("SELECT has_function_privilege('public','crm_register_ai_client(text,text,json,text,json)','EXECUTE')")).scalar_one()


@pytest.mark.parametrize('kind,scopes,ident', [
    ('operator',('clients:read',),'new'), ('dcr',('reports:read',),'new'), ('dcr',(),'new'),
    ('cimd',('clients:read',),'chatgpt'), ('dcr',('clients:read',),'chatgpt')])
def test_function_refuses_trust_or_scope_escalation(legacy,kind,scopes,ident):
    engine,schema=legacy;role=os.environ['SECURITY_TEST_ROLE']
    upgrade(engine,schema,role)
    with engine.connect() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        c.execute(text(f'SET LOCAL ROLE "{role}"'))
        with pytest.raises(DBAPIError) as rejected:
            register(c,ident,kind,scopes)
        assert rejected.value.orig.pgcode in ('22023','23505')
    with engine.connect() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        row=c.execute(text("SELECT name,registration,inline_review,allowed_scopes::text FROM ai_clients WHERE id='chatgpt'")).one()
        assert row==('ChatGPT','operator',True,'["clients:read"]')
        assert c.execute(text("SELECT count(*) FROM ai_clients WHERE id='new'")).scalar_one()==0


def test_runtime_registers_and_refreshes_without_gaining_trust(legacy):
    engine,schema=legacy;role=os.environ['SECURITY_TEST_ROLE']
    upgrade(engine,schema,role)
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        c.execute(text(f'SET LOCAL ROLE "{role}"'))
        register(c,'https://agent.example/c.json','cimd',('clients:read','leads:read'))
        register(c,'https://agent.example/c.json','cimd',('leads:read',),('https://agent.example/new',))
        with pytest.raises(DBAPIError) as denied:
            with c.begin_nested():
                c.execute(text("UPDATE ai_clients SET inline_review=true"))
        assert denied.value.orig.pgcode=='42501'
    with engine.connect() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        row=c.execute(text("SELECT registration,inline_review,is_active,oauth_enabled,redirect_uris::text,allowed_scopes::text "
                           "FROM ai_clients WHERE id='https://agent.example/c.json'")).one()
        assert row==('cimd',False,True,True,'["https://agent.example/new"]','["leads:read"]')
        with pytest.raises(DBAPIError) as violated:
            c.execute(text("UPDATE ai_clients SET inline_review=true WHERE id='https://agent.example/c.json'"))
        assert violated.value.orig.pgcode=='23514'


@pytest.mark.parametrize('failure',['missing_role','unsafe_role','existing_columns'])
def test_failed_migration_preserves_revision_and_clients(legacy,failure):
    engine,schema=legacy
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        role=None if failure=='missing_role' else c.execute(text('SELECT current_user')).scalar_one() if failure=='unsafe_role' else os.environ['SECURITY_TEST_ROLE']
        if failure=='existing_columns': c.execute(text('ALTER TABLE ai_clients ADD COLUMN registration text'))
    with pytest.raises(RuntimeError): upgrade(engine,schema,role)
    with engine.connect() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        assert c.execute(text('SELECT version_num FROM alembic_version')).scalar_one()==down_revision
        assert c.execute(text('SELECT id FROM ai_clients ORDER BY id')).scalars().all()==['chatgpt','offline']
        columns={column['name'] for column in inspect(c).get_columns('ai_clients',schema=schema)}
        assert ('inline_review' in columns) is False
