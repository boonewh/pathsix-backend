"""Rehearse the real additive migration, its grants and rollback boundaries."""
import os
from pathlib import Path
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text, inspect
from test_mcp_audit_migration import legacy as audit_legacy, upgrade as audit_upgrade
from test_oauth_migration import consent_legacy, legacy as oauth_legacy
from migrations.versions.mcp_lead_creation import revision, down_revision, reconcile


@pytest.fixture
def legacy(audit_legacy):
    engine,schema=audit_legacy
    audit_upgrade(engine,schema,os.environ['SECURITY_TEST_ROLE'])
    return engine,schema


def upgrade(engine,schema,role):
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        root=Path(__file__).resolve().parents[1]
        cfg=Config(str(root/'alembic.ini'))
        cfg.set_main_option('script_location',str(root/'migrations'))
        cfg.attributes.update(connection=c,version_table_schema=schema,runtime_role=role)
        command.upgrade(cfg,revision)


def test_rehearsal_rollback_then_migration(legacy):
    engine,schema=legacy;role=os.environ['SECURITY_TEST_ROLE']
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        with c.begin_nested() as rehearsal:
            reconcile(c,schema,role)
            assert 'ai_write_actions' in inspect(c).get_table_names(schema=schema)
            rehearsal.rollback()
        assert 'ai_write_actions' not in inspect(c).get_table_names(schema=schema)
    upgrade(engine,schema,role)
    with engine.connect() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        assert c.execute(text('SELECT version_num FROM alembic_version')).scalar_one()==revision
        assert c.execute(text("SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE oid='ai_write_actions'::regclass")).scalar_one()
        for column in ('status','decided_at','result_id','payload','tenant_id','request_key','expires_at'):
            allowed=c.execute(text("SELECT has_column_privilege(:role,:table,:column,'UPDATE')"),
                {'role':role,'table':schema+'.ai_write_actions','column':column}).scalar_one()
            assert allowed==(column in ('status','decided_at','result_id'))
        for privilege in ('DELETE','TRUNCATE'):
            assert not c.execute(text('SELECT has_table_privilege(:role,:table,:privilege)'),
                {'role':role,'table':schema+'.ai_write_actions','privilege':privilege}).scalar_one()
        assert c.execute(text('SELECT id FROM users ORDER BY id')).scalars().all()==[1,2]


@pytest.mark.parametrize('failure',['missing_role','unsafe_role','existing_table'])
def test_failed_migration_preserves_revision_and_data(legacy,failure):
    engine,schema=legacy
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        role=None if failure=='missing_role' else c.execute(text('SELECT current_user')).scalar_one() if failure=='unsafe_role' else os.environ['SECURITY_TEST_ROLE']
        if failure=='existing_table': c.execute(text('CREATE TABLE ai_write_actions (sentinel integer)'))
    with pytest.raises(RuntimeError): upgrade(engine,schema,role)
    with engine.connect() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        assert c.execute(text('SELECT version_num FROM alembic_version')).scalar_one()==down_revision
        assert c.execute(text('SELECT id FROM users ORDER BY id')).scalars().all()==[1,2]
        assert ('ai_write_actions' in inspect(c).get_table_names(schema=schema))==(failure=='existing_table')
