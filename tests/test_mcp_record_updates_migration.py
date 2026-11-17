"""The record-updates migration allows update kinds and publishes update scopes."""
import json
import os
from pathlib import Path
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_mcp_record_creation_migration import legacy as creation_legacy, upgrade as creation_upgrade
from test_mcp_published_scopes_migration import legacy as scopes_legacy
from test_ai_connection_idle_migration import legacy as idle_legacy
from test_mcp_client_registration_migration import legacy as registration_legacy
from test_mcp_lead_migration import legacy as lead_legacy
from test_mcp_audit_migration import legacy as audit_legacy
from test_oauth_migration import consent_legacy, legacy as oauth_legacy
from migrations.versions.mcp_record_updates import revision, KINDS, PUBLISHED
from migrations.versions import mcp_record_updates


@pytest.fixture
def legacy(creation_legacy):
    engine, schema = creation_legacy
    creation_upgrade(engine, schema)
    return engine, schema


def upgrade(engine, schema):
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        root = Path(mcp_record_updates.__file__).resolve().parents[2]
        cfg = Config(str(root / 'alembic.ini'))
        cfg.set_main_option('script_location', str(root / 'migrations'))
        cfg.attributes.update(connection=c, version_table_schema=schema, runtime_role=os.environ['SECURITY_TEST_ROLE'])
        command.upgrade(cfg, revision)


def test_update_kinds_allowed_and_update_scopes_published(legacy):
    engine, schema = legacy
    upgrade(engine, schema)
    with engine.connect() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        assert c.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == revision
        definition = c.execute(text("SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                                    "WHERE conname='ck_ai_write_actions_kind'")).scalar_one()
        assert all(kind in definition for kind in KINDS) and 'delete' not in definition
        scopes = json.loads(c.execute(text("SELECT allowed_scopes::text FROM ai_clients "
                                           "WHERE id='https://agent.example/c.json'")).scalar_one())
        assert set(scopes) == set(PUBLISHED)
        c.execute(text(f'SET LOCAL ROLE "{os.environ["SECURITY_TEST_ROLE"]}"'))
        c.execute(text("SELECT crm_register_ai_client('dcr-w','W',CAST('[\"http://127.0.0.1/cb\"]' AS json),'dcr',"
                       "CAST('[\"leads:update\",\"clients:update\",\"interactions:update\"]' AS json))"))
        with pytest.raises(DBAPIError):
            c.execute(text("SELECT crm_register_ai_client('dcr-x','X',CAST('[\"http://127.0.0.1/cb\"]' AS json),'dcr',"
                           "CAST('[\"clients:create\"]' AS json))"))
