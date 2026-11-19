"""Stable UUID backfill, rollback safety and runtime-role access in real DDL."""
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError

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

# Independent of the migration's own list, so dropping a table there fails here.
SYNCED = ('accounts', 'clients', 'contacts', 'interactions', 'leads', 'projects')
TENANT = "NULLIF(current_setting('crm.tenant_id', true), '')::integer"  # as tenant_rls_prepare


@pytest.fixture
def legacy(updates_legacy):
    engine, schema = updates_legacy
    updates_upgrade(engine, schema)
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        existing = set(inspect(c).get_table_names(schema=schema))
        for table in SYNCED:
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
    return {table: c.execute(text(f'SELECT count(*) FROM {table}')).scalar_one() for table in SYNCED}


def _ids(c):
    return {table: list(c.execute(text(f'SELECT id FROM {table} ORDER BY id')).scalars()) for table in SYNCED}


def _columns(c, schema):
    return {table: sorted(col['name'] for col in inspect(c).get_columns(table, schema=schema)) for table in SYNCED}


def test_migration_covers_exactly_the_synced_tables():
    assert set(TABLES) == set(SYNCED)


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
        for table in SYNCED:
            values = [str(value) if value is not None else None
                      for value in c.execute(text(f'SELECT uuid FROM {table}')).scalars()]
            assert len(values) == before[table]
            assert all(values)
            assert len(set(values)) == len(values)


def test_existing_uuid_column_aborts_without_changing_history_or_rows(legacy):
    engine, schema = legacy
    last = TABLES[-1]  # the preflight must refuse before any earlier table is altered
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        c.execute(text(f'ALTER TABLE {last} ADD COLUMN uuid text'))
        ids_before, columns_before = _ids(c), _columns(c, schema)

    with pytest.raises(RuntimeError, match='Existing uuid column'):
        upgrade(engine, schema)

    with engine.connect() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        assert c.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == down_revision
        assert _ids(c) == ids_before
        assert _columns(c, schema) == columns_before


def test_existing_grants_and_tenant_policy_cover_uuid_after_migration(legacy):
    """Grants and row security are set up BEFORE upgrading, as on a real database."""
    engine, schema = legacy
    role = os.environ['SECURITY_TEST_ROLE']
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        q = c.dialect.identifier_preparer.quote
        c.execute(text('ALTER TABLE clients ADD COLUMN IF NOT EXISTS tenant_id integer'))
        c.execute(text('UPDATE clients SET tenant_id = 1'))
        next_id = c.execute(text('SELECT MAX(id) + 1 FROM clients')).scalar_one()
        c.execute(text('INSERT INTO clients (id, tenant_id) VALUES (:id, 2)'), {'id': next_id})
        if not c.execute(text("SELECT count(*) FROM pg_policies WHERE schemaname=:s AND tablename='clients'"),
                         {'s': schema}).scalar_one():
            c.execute(text(f'CREATE POLICY crm_all ON clients FOR ALL TO PUBLIC '
                           f'USING (tenant_id={TENANT}) WITH CHECK (tenant_id={TENANT})'))
        c.execute(text('ALTER TABLE clients ENABLE ROW LEVEL SECURITY'))
        c.execute(text('ALTER TABLE clients FORCE ROW LEVEL SECURITY'))
        c.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO {q(role)}'))
        c.execute(text(f'GRANT SELECT, INSERT, UPDATE ON clients TO {q(role)}'))

    upgrade(engine, schema)

    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        c.execute(text(f'SET LOCAL ROLE {c.dialect.identifier_preparer.quote(role)}'))
        c.execute(text("SELECT set_config('crm.tenant_id', '1', true)"))
        visible = c.execute(text('SELECT tenant_id, uuid FROM clients')).all()
        assert visible and {row.tenant_id for row in visible} == {1}
        assert all(row.uuid is not None for row in visible)
        inserted = c.execute(text('INSERT INTO clients (id, tenant_id) VALUES (:id, 1) RETURNING uuid'),
                             {'id': 999}).scalar_one()
        assert inserted is not None and inserted not in {row.uuid for row in visible}
        with c.begin_nested():
            with pytest.raises(DBAPIError) as denied:
                c.execute(text('INSERT INTO clients (id, tenant_id) VALUES (:id, 2)'), {'id': 1000})
        assert denied.value.orig.pgcode == '42501'


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
