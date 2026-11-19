"""Stable UUID backfill, rollback safety and runtime-role access in real DDL."""
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from test_mcp_record_updates_migration import legacy as updates_legacy, upgrade as updates_upgrade
from test_mcp_record_creation_migration import legacy as creation_legacy
from test_mcp_published_scopes_migration import legacy as scopes_legacy
from test_ai_connection_idle_migration import legacy as idle_legacy
from test_mcp_client_registration_migration import legacy as registration_legacy
from test_mcp_lead_migration import legacy as lead_legacy
from test_mcp_audit_migration import legacy as audit_legacy
from test_oauth_migration import consent_legacy, legacy as oauth_legacy
from migrations.versions import mobile_stable_ids
from migrations.versions.mobile_stable_ids import TABLES, revision, down_revision


@pytest.fixture
def legacy(updates_legacy):
    engine, schema = updates_legacy
    updates_upgrade(engine, schema)
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        existing = set(inspect(c).get_table_names(schema=schema))
        for table in TABLES:
            if table not in existing:
                c.execute(text(f'CREATE TABLE {table} (id integer PRIMARY KEY)'))
            next_id = c.execute(text(f'SELECT COALESCE(MAX(id), 0) + 1 FROM {table}')).scalar_one()
            c.execute(text(f'INSERT INTO {table} (id) VALUES (:a), (:b), (:c)'),
                      {'a': next_id, 'b': next_id + 1, 'c': next_id + 2})
    return engine, schema


def upgrade(engine, schema):
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        root = Path(mobile_stable_ids.__file__).resolve().parents[2]
        cfg = Config(str(root / 'alembic.ini'))
        cfg.set_main_option('script_location', str(root / 'migrations'))
        cfg.attributes.update(connection=c, version_table_schema=schema,
                              runtime_role=os.environ['SECURITY_TEST_ROLE'])
        command.upgrade(cfg, revision)


def _counts(c):
    return {table: c.execute(text(f'SELECT count(*) FROM {table}')).scalar_one() for table in TABLES}


def test_backfills_a_distinct_uuid_for_every_existing_row(legacy):
    engine, schema = legacy
    with engine.connect() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        before = _counts(c)
    assert all(count >= 3 for count in before.values())

    upgrade(engine, schema)

    with engine.connect() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        assert c.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == revision
        for table in TABLES:
            values = [str(value) if value is not None else None
                      for value in c.execute(text(f'SELECT uuid FROM {table}')).scalars()]
            assert len(values) == before[table]
            assert all(values)
            assert len(set(values)) == len(values)


def test_existing_uuid_column_aborts_without_changing_history_or_rows(legacy):
    engine, schema = legacy
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        c.execute(text('ALTER TABLE clients ADD COLUMN uuid text'))
        before = _counts(c)

    with pytest.raises(RuntimeError, match='Existing uuid column'):
        upgrade(engine, schema)

    with engine.connect() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        assert c.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == down_revision
        assert _counts(c) == before
        assert 'uuid' not in {column['name'] for column in inspect(c).get_columns('projects', schema=schema)}


def test_runtime_role_reads_uuid_and_inserts_with_generated_uuid(legacy):
    engine, schema = legacy
    upgrade(engine, schema)
    role = os.environ['SECURITY_TEST_ROLE']
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        quoted_role = c.dialect.identifier_preparer.quote(role)
        c.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO {quoted_role}'))
        c.execute(text(f'GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA "{schema}" TO {quoted_role}'))
        c.execute(text(f'SET LOCAL ROLE {quoted_role}'))
        existing = c.execute(text('SELECT uuid FROM clients LIMIT 1')).scalar_one()
        assert existing is not None
        inserted = c.execute(text('INSERT INTO clients (id) VALUES (:id) RETURNING uuid'),
                             {'id': 999}).scalar_one()
        assert inserted is not None
        assert str(inserted) != str(existing)


def test_duplicate_uuid_violates_the_unique_constraint(legacy):
    engine, schema = legacy
    upgrade(engine, schema)
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        existing = c.execute(text('SELECT uuid FROM clients LIMIT 1')).scalar_one()
        with c.begin_nested() as savepoint:
            with pytest.raises(IntegrityError) as exc:
                c.execute(text('INSERT INTO clients (id, uuid) VALUES (:id, :uuid)'),
                          {'id': 998, 'uuid': existing})
            assert exc.value.orig.pgcode == '23505'
            savepoint.rollback()
        assert c.execute(text('SELECT count(*) FROM clients')).scalar_one() == 3
