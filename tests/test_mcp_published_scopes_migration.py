"""The published-scopes migration widens self-registered clients only."""
import json
import os
from pathlib import Path
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_ai_connection_idle_migration import legacy as idle_legacy, upgrade as idle_upgrade
from test_mcp_client_registration_migration import legacy as registration_legacy
from test_mcp_lead_migration import legacy as lead_legacy
from test_mcp_audit_migration import legacy as audit_legacy
from test_oauth_migration import consent_legacy, legacy as oauth_legacy
from migrations.versions.mcp_published_scopes_v2 import revision, PUBLISHED
from migrations.versions import mcp_published_scopes_v2


@pytest.fixture
def legacy(idle_legacy):
    engine, schema = idle_legacy
    idle_upgrade(engine, schema)
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        c.execute(text(f'SET LOCAL ROLE "{os.environ["SECURITY_TEST_ROLE"]}"'))
        c.execute(text("SELECT crm_register_ai_client('https://agent.example/c.json','Agent',"
                       "CAST('[\"https://agent.example/cb\"]' AS json),'cimd',CAST('[\"clients:read\"]' AS json))"))
    return engine, schema


def upgrade(engine, schema):
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        root = Path(mcp_published_scopes_v2.__file__).resolve().parents[2]
        cfg = Config(str(root / 'alembic.ini'))
        cfg.set_main_option('script_location', str(root / 'migrations'))
        cfg.attributes.update(connection=c, version_table_schema=schema, runtime_role=os.environ['SECURITY_TEST_ROLE'])
        command.upgrade(cfg, revision)


def test_new_scopes_published_for_self_registered_clients_only(legacy):
    engine, schema = legacy
    upgrade(engine, schema)
    with engine.connect() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        assert c.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == revision
        scopes = dict(c.execute(text('SELECT id, allowed_scopes::text FROM ai_clients')).all())
        assert set(json.loads(scopes['https://agent.example/c.json'])) == set(PUBLISHED)
        assert json.loads(scopes['chatgpt']) == ['clients:read']  # Operator rows change only deliberately.
        c.execute(text(f'SET LOCAL ROLE "{os.environ["SECURITY_TEST_ROLE"]}"'))
        c.execute(text("SELECT crm_register_ai_client('dcr-new','New',CAST('[\"http://127.0.0.1/cb\"]' AS json),"
                       "'dcr',CAST('[\"contacts:read\",\"projects:read\",\"interactions:read\"]' AS json))"))
        with pytest.raises(DBAPIError) as refused:
            c.execute(text("SELECT crm_register_ai_client('dcr-bad','Bad',CAST('[\"http://127.0.0.1/cb\"]' AS json),"
                           "'dcr',CAST('[\"reports:read\"]' AS json))"))
        assert refused.value.orig.pgcode == '22023'
