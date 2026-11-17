"""Exercise real DDL/version advancement and transactional failures, isolated only."""
import os
from pathlib import Path
from uuid import uuid4
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text, inspect
from migrations.versions.ai_consent_grants import revision, down_revision


@pytest.fixture
def legacy():
    url=os.getenv('SECURITY_TEST_DATABASE_URL')
    if not url or not os.getenv('SECURITY_TEST_ROLE'):
        pytest.skip('Requires PostgreSQL restricted role')
    engine=create_engine(url,hide_parameters=True)
    schema='security_test_'+uuid4().hex
    with engine.begin() as c:
        c.execute(text(f'CREATE SCHEMA "{schema}"'))
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        c.execute(text('CREATE TABLE tenants (id integer PRIMARY KEY)'))
        c.execute(text('CREATE TABLE users (id integer PRIMARY KEY, tenant_id integer NOT NULL, UNIQUE(tenant_id,id))'))
        c.execute(text('CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY)'))
        c.execute(text('INSERT INTO alembic_version VALUES (:head)'),{'head':down_revision})
        c.execute(text('INSERT INTO tenants VALUES (1),(2)'))
        c.execute(text('INSERT INTO users VALUES (1,1),(2,2)'))
    try: yield engine,schema
    finally:
        with engine.begin() as c: c.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


def upgrade(engine,schema,role):
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        root=Path(__file__).resolve().parents[1]
        # Staging verification copies tests to /tmp; application lives in /app.
        if not (root/'alembic.ini').exists(): root=Path('/app')
        cfg=Config(str(root/'alembic.ini'))
        cfg.set_main_option('script_location',str(root/'migrations'))
        cfg.attributes.update(connection=c,version_table_schema=schema,runtime_role=role)
        command.upgrade(cfg,revision)


def test_migration_creates_owner_constraints_policies_and_history(legacy):
    engine,schema=legacy
    upgrade(engine,schema,os.environ['SECURITY_TEST_ROLE'])
    with engine.connect() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        assert c.execute(text('SELECT version_num FROM alembic_version')).scalar_one()==revision
        assert c.execute(text('SELECT count(*) FROM ai_connections')).scalar_one()==0
        assert c.execute(text("SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE oid='ai_connections'::regclass")).scalar_one()
        constraints=inspect(c).get_foreign_keys('ai_connections',schema=schema)
        assert any(x['constrained_columns']==['tenant_id','user_id'] and x['referred_columns']==['tenant_id','id'] for x in constraints)
        role=os.environ['SECURITY_TEST_ROLE']
        for privilege in ('UPDATE','DELETE','INSERT'):
            assert not c.execute(text('SELECT has_table_privilege(:role,:table,:privilege)'),{'role':role,'table':schema+'.ai_clients','privilege':privilege}).scalar_one()
        assert not c.execute(text('SELECT has_table_privilege(:role,:table,\'DELETE\')'),{'role':role,'table':schema+'.ai_connections'}).scalar_one()


@pytest.mark.parametrize('failure',['missing_role','unsafe_role','existing_table'])
def test_migration_failure_keeps_data_and_version(legacy,failure):
    engine,schema=legacy
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        role=None if failure=='missing_role' else c.execute(text('SELECT current_user')).scalar_one() if failure=='unsafe_role' else os.environ['SECURITY_TEST_ROLE']
        if failure=='existing_table': c.execute(text('CREATE TABLE ai_clients (sentinel integer)'))
    with pytest.raises(RuntimeError): upgrade(engine,schema,role)
    with engine.connect() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        assert c.execute(text('SELECT version_num FROM alembic_version')).scalar_one()==down_revision
        assert c.execute(text('SELECT id FROM users ORDER BY id')).scalars().all()==[1,2]
        names=inspect(c).get_table_names(schema=schema)
        assert 'ai_connections' not in names
        assert ('ai_clients' in names)==(failure=='existing_table')
