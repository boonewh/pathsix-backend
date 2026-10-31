"""Rehearse the inactivity-expiry migration on a grant approved before it."""
import os
from pathlib import Path
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text, inspect
from sqlalchemy.exc import DBAPIError
from test_mcp_client_registration_migration import legacy as registration_legacy, upgrade as registration_upgrade
from test_mcp_lead_migration import legacy as lead_legacy
from test_mcp_audit_migration import legacy as audit_legacy
from test_oauth_migration import consent_legacy, legacy as oauth_legacy
from migrations.versions.ai_connection_idle_expiry import revision, down_revision
from migrations.versions import ai_connection_idle_expiry


@pytest.fixture
def legacy(registration_legacy):
    engine,schema=registration_legacy
    registration_upgrade(engine,schema,os.environ['SECURITY_TEST_ROLE'])
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        tenant=c.execute(text('SELECT tenant_id FROM users WHERE id=1')).scalar_one()
        c.execute(text("INSERT INTO ai_connections (id,tenant_id,user_id,client_id,resource,scopes,created_at,expires_at) "
                       "VALUES ('00000000-0000-0000-0000-000000000001',:t,1,'chatgpt','https://x.test/mcp','[\"clients:read\"]',"
                       "'2026-10-01','2026-10-31')"),{'t':tenant})
    return engine,schema


def upgrade(engine,schema):
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        root=Path(ai_connection_idle_expiry.__file__).resolve().parents[2]
        cfg=Config(str(root/'alembic.ini'))
        cfg.set_main_option('script_location',str(root/'migrations'))
        cfg.attributes.update(connection=c,version_table_schema=schema,runtime_role=os.environ['SECURITY_TEST_ROLE'])
        command.upgrade(cfg,revision)


def test_existing_grants_keep_their_lifetime_and_limits_hold(legacy):
    engine,schema=legacy
    upgrade(engine,schema)
    with engine.connect() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        assert c.execute(text('SELECT version_num FROM alembic_version')).scalar_one()==revision
        assert c.execute(text('SELECT active_until=expires_at FROM ai_connections')).scalar_one()
        with pytest.raises(DBAPIError) as beyond:
            with c.begin_nested():
                c.execute(text("UPDATE ai_connections SET active_until=expires_at+interval '1 day'"))
        assert beyond.value.orig.pgcode=='23514'
        with pytest.raises(DBAPIError) as absolute:
            with c.begin_nested():
                c.execute(text("UPDATE ai_connections SET expires_at=expires_at+interval '1 day'"))
        assert absolute.value.orig.pgcode=='23514'
        c.execute(text("UPDATE ai_connections SET active_until=expires_at-interval '1 day'"))


def test_failed_migration_preserves_revision(legacy):
    engine,schema=legacy
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        c.execute(text('ALTER TABLE ai_connections ADD COLUMN active_until timestamp'))
    with pytest.raises(RuntimeError): upgrade(engine,schema)
    with engine.connect() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        assert c.execute(text('SELECT version_num FROM alembic_version')).scalar_one()==down_revision
        assert c.execute(text('SELECT count(*) FROM ai_connections')).scalar_one()==1
