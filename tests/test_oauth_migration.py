"""Real DDL and rollback checks; synthetic schemas only."""
import os
from pathlib import Path
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text, inspect
from test_ai_consent_migration import legacy as consent_legacy, upgrade as consent_upgrade
from migrations.versions.oauth_browser_flow import revision, down_revision


@pytest.fixture
def legacy(consent_legacy):
    engine,schema=consent_legacy
    consent_upgrade(engine,schema,os.environ['SECURITY_TEST_ROLE'])
    return engine,schema


def upgrade(engine,schema,role):
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        root=Path(__file__).resolve().parents[1]
        if not (root/'alembic.ini').exists(): root=Path('/app')
        cfg=Config(str(root/'alembic.ini'))
        cfg.set_main_option('script_location',str(root/'migrations'))
        cfg.attributes.update(connection=c,version_table_schema=schema,runtime_role=role)
        command.upgrade(cfg,revision)


def test_oauth_migration_creates_private_immutable_credentials(legacy):
    engine,schema=legacy
    role=os.environ['SECURITY_TEST_ROLE']
    upgrade(engine,schema,role)
    with engine.connect() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        assert c.execute(text('SELECT version_num FROM alembic_version')).scalar_one()==revision
        assert c.execute(text('SELECT count(*) FROM oauth_credentials')).scalar_one()==0
        assert c.execute(text("SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE oid='oauth_credentials'::regclass")).scalar_one()
        constraints=inspect(c).get_foreign_keys('oauth_credentials',schema=schema)
        assert any(x['constrained_columns']==['tenant_id','user_id','connection_id'] for x in constraints)
        assert not c.execute(text("SELECT has_table_privilege(:role,:table,'DELETE')"),{'role':role,'table':schema+'.oauth_credentials'}).scalar_one()
        function=c.execute(text("SELECT proargnames,prosecdef,proconfig,proacl::text FROM pg_proc WHERE oid='crm_oauth_identity(text)'::regprocedure")).one()
        assert function[0]==['p_hash','user_id','tenant_id'] and function[1] and function[2]==['search_path=pg_catalog']
        assert c.execute(text("SELECT count(*) FROM pg_proc p, LATERAL aclexplode(p.proacl) a WHERE p.oid='crm_oauth_identity(text)'::regprocedure AND a.grantee=0 AND a.privilege_type='EXECUTE'")).scalar_one()==0
        assert c.execute(text("SELECT count(*) FROM crm_oauth_identity('unknown')")).scalar_one()==0


@pytest.mark.parametrize('failure',['missing_role','unsafe_role','existing_table'])
def test_oauth_migration_failure_is_transactional(legacy,failure):
    engine,schema=legacy
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        role=None if failure=='missing_role' else c.execute(text('SELECT current_user')).scalar_one() if failure=='unsafe_role' else os.environ['SECURITY_TEST_ROLE']
        if failure=='existing_table': c.execute(text('CREATE TABLE oauth_credentials (sentinel integer)'))
    with pytest.raises(RuntimeError): upgrade(engine,schema,role)
    with engine.connect() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        assert c.execute(text('SELECT version_num FROM alembic_version')).scalar_one()==down_revision
        assert c.execute(text('SELECT id FROM users ORDER BY id')).scalars().all()==[1,2]
        assert 'oauth_enabled' not in {column['name'] for column in inspect(c).get_columns('ai_clients',schema=schema)}
