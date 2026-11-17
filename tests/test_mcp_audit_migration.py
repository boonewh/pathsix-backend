"""Real PostgreSQL audit migration and transactional rollback checks."""
import os
from pathlib import Path
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text, inspect
from test_oauth_migration import consent_legacy, legacy as oauth_legacy, upgrade as oauth_upgrade
from migrations.versions.mcp_read_audit import revision, down_revision


@pytest.fixture
def legacy(oauth_legacy):
    engine,schema=oauth_legacy
    oauth_upgrade(engine,schema,os.environ['SECURITY_TEST_ROLE'])
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


def test_migration_creates_append_only_owner_audits(legacy):
    engine,schema=legacy
    role=os.environ['SECURITY_TEST_ROLE']
    upgrade(engine,schema,role)
    with engine.connect() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        assert c.execute(text('SELECT version_num FROM alembic_version')).scalar_one()==revision
        assert c.execute(text('SELECT count(*) FROM ai_tool_audits')).scalar_one()==0
        assert c.execute(text("SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE oid='ai_tool_audits'::regclass")).scalar_one()
        constraints=inspect(c).get_foreign_keys('ai_tool_audits',schema=schema)
        assert any(x['constrained_columns']==['tenant_id','user_id','connection_id'] for x in constraints)
        for privilege in ('SELECT','INSERT','UPDATE','DELETE','TRUNCATE'):
            allowed=c.execute(text('SELECT has_table_privilege(:role,:table,:privilege)'),
                {'role':role,'table':schema+'.ai_tool_audits','privilege':privilege}).scalar_one()
            assert allowed==(privilege in ('SELECT','INSERT'))


@pytest.mark.parametrize('failure',['missing_role','unsafe_role','existing_table'])
def test_migration_failure_preserves_prior_revision(legacy,failure):
    engine,schema=legacy
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        role=None if failure=='missing_role' else c.execute(text('SELECT current_user')).scalar_one() if failure=='unsafe_role' else os.environ['SECURITY_TEST_ROLE']
        if failure=='existing_table': c.execute(text('CREATE TABLE ai_tool_audits (sentinel integer)'))
    with pytest.raises(RuntimeError): upgrade(engine,schema,role)
    with engine.connect() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        assert c.execute(text('SELECT version_num FROM alembic_version')).scalar_one()==down_revision
        assert c.execute(text('SELECT id FROM users ORDER BY id')).scalars().all()==[1,2]
        assert ('ai_tool_audits' in inspect(c).get_table_names(schema=schema))==(failure=='existing_table')
