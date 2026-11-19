"""Change-tracking DDL, backfill, constraints and runtime access in real PostgreSQL."""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from migrations.versions import mobile_change_tracking
from migrations.versions.mobile_stable_ids import revision as previous_revision
# The stable-id legacy fixture resolves these aliases in this module, so repeat
# the chain imports it expects.
from test_mcp_record_updates_migration import legacy as updates_legacy
from test_mcp_record_creation_migration import legacy as creation_legacy
from test_mcp_published_scopes_migration import legacy as scopes_legacy
from test_ai_connection_idle_migration import legacy as idle_legacy
from test_mcp_client_registration_migration import legacy as registration_legacy
from test_mcp_lead_migration import legacy as lead_legacy
from test_mcp_audit_migration import legacy as audit_legacy
from test_oauth_migration import consent_legacy, legacy as oauth_legacy
from test_mobile_stable_ids_migration import (
    legacy as stable_ids_legacy,
    upgrade as stable_ids_upgrade,
)

# Independent of the migration's SYNCED constant: removing a table from the
# migration must make these tests fail.
SYNCED = ('accounts', 'clients', 'contacts', 'interactions', 'leads', 'projects')
ENTITY = {
    'accounts': 'account',
    'clients': 'client',
    'contacts': 'contact',
    'interactions': 'interaction',
    'leads': 'lead',
    'projects': 'project',
}
# The attribute sales_audit uses as each activity description, so the seeded
# entries look genuine; the migration must ignore them anyway.
NAME_COLUMN = {
    'accounts': 'account_name',
    'clients': 'name',
    'contacts': 'first_name',
    'interactions': 'summary',
    'leads': 'name',
    'projects': 'project_name',
}
LABEL = {
    'accounts': 'Account',
    'clients': 'Client',
    'contacts': 'Contact',
    'interactions': 'Interaction',
    'leads': 'Lead',
    'projects': 'Project',
}
TENANT = "NULLIF(current_setting('crm.tenant_id', true), '')::integer"
OLD = datetime(2000, 1, 1, 0, 0, 0)
OLD_UPDATED = datetime(2001, 6, 1, 12, 0, 0)
# PostgreSQL now() and the Python process clock differ by sub-milliseconds.
CLOCK_SLOP = timedelta(seconds=2)
EARLIEST = datetime(2020, 1, 1, 9, 0, 0)
LATER = datetime(2020, 1, 2, 9, 0, 0)
CROSS_TENANT = datetime(2019, 1, 1, 9, 0, 0)
EDITED = datetime(2018, 1, 1, 9, 0, 0)
WRONG_USER = datetime(2020, 1, 3, 9, 0, 0)


@pytest.fixture
def legacy(stable_ids_legacy):
    """Schema immediately before mobile_change_tracking, with realistic legacy rows."""
    engine, schema = stable_ids_legacy
    stable_ids_upgrade(engine, schema)
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        for table in SYNCED:
            c.execute(text(f'ALTER TABLE {table} ADD COLUMN tenant_id integer'))
        for table in ('clients', 'leads', 'projects', 'contacts'):
            c.execute(text(f'ALTER TABLE {table} ADD COLUMN created_at timestamp'))
        for table in ('clients', 'leads', 'projects'):
            c.execute(text(f'ALTER TABLE {table} ADD COLUMN updated_at timestamp'))
        for table, column in NAME_COLUMN.items():
            c.execute(text(f'ALTER TABLE {table} ADD COLUMN {column} text'))
        c.execute(text(
            'CREATE TABLE activity_logs ('
            'id integer PRIMARY KEY, tenant_id integer NOT NULL, '
            'user_id integer NOT NULL, action text NOT NULL, '
            'entity_type text NOT NULL, entity_id integer NOT NULL, '
            '"timestamp" timestamp NOT NULL, description text NOT NULL)'))
        # tenant 1: users 1 and 3; tenant 2: user 2. The stable-id chain supplies 1 and 2.
        c.execute(text('INSERT INTO users (id, tenant_id) VALUES (3, 1)'))

        # The stable-id fixture supplies ids 1-3 in every synced table. Add a
        # tenant-2 row with no activity and an interaction whose activity entry
        # has a stale description, then assign tenants to all legacy rows.
        for table in SYNCED:
            c.execute(text(f'INSERT INTO {table} (id, tenant_id) VALUES (4, 2)'))
            c.execute(text(f'UPDATE {table} SET tenant_id = 1 WHERE id < 4'))
            c.execute(text(f"UPDATE {table} SET {NAME_COLUMN[table]} = "
                           f"'{LABEL[table]} ' || id"))
        c.execute(text('INSERT INTO interactions (id, tenant_id) VALUES (5, 1)'))
        c.execute(text("UPDATE interactions SET summary = 'Interaction 5' WHERE id = 5"))
        for table in ('clients', 'leads', 'projects', 'contacts'):
            c.execute(text(f'UPDATE {table} SET created_at = :at WHERE id = 1'), {'at': OLD})
        for table in ('clients', 'leads', 'projects'):
            c.execute(text(f'UPDATE {table} SET updated_at = :at WHERE id = 1'), {'at': OLD_UPDATED})

        rows = []
        row_id = 1
        for table in SYNCED:
            entity = ENTITY[table]
            description = f'{LABEL[table]} 2'
            rows.extend([
                (row_id, 1, 1, 'created', entity, 2, EARLIEST, description),
                (row_id + 1, 1, 3, 'created', entity, 2, LATER, description),
                (row_id + 2, 2, 2, 'created', entity, 2, CROSS_TENANT, description),
            ])
            row_id += 3
        rows.extend([
            (row_id, 1, 1, 'edited', 'client', 2, EDITED, 'Client 2'),
            (row_id + 1, 1, 2, 'created', 'interaction', 3, WRONG_USER, 'Interaction 3'),
            # Same tenant and entity_id, but the description no longer names the
            # record: the migration must fall back instead of trusting the id.
            (row_id + 2, 1, 1, 'created', 'interaction', 5, EARLIEST, 'Stale summary'),
        ])
        fields = ('id', 'tenant_id', 'user_id', 'action', 'entity_type',
                  'entity_id', 'at', 'description')
        c.execute(text(
            'INSERT INTO activity_logs '
            '(id, tenant_id, user_id, action, entity_type, entity_id, "timestamp", description) '
            'VALUES (:id, :tenant_id, :user_id, :action, :entity_type, :entity_id, :at, :description)'),
            [dict(zip(fields, row)) for row in rows])
    return engine, schema


def upgrade(engine, schema):
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        root = Path(mobile_change_tracking.__file__).resolve().parents[2]
        cfg = Config(str(root / 'alembic.ini'))
        cfg.set_main_option('script_location', str(root / 'migrations'))
        cfg.attributes.update(connection=c, version_table_schema=schema,
                              runtime_role=os.environ['SECURITY_TEST_ROLE'])
        command.upgrade(cfg, mobile_change_tracking.revision)


def _ids(c):
    return {table: list(c.execute(text(f'SELECT id FROM {table} ORDER BY id')).scalars())
            for table in SYNCED}


def _columns(c, schema):
    inspector = inspect(c)
    return {table: sorted(column['name'] for column in inspector.get_columns(table, schema=schema))
            for table in SYNCED}


def _utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _assert_recent(value, before, after):
    assert before - CLOCK_SLOP <= value <= after + CLOCK_SLOP


def test_timestamp_columns_are_non_null_with_utc_defaults_and_creator_nullable(legacy):
    engine, schema = legacy
    upgrade(engine, schema)
    with engine.connect() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        assert c.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == \
            mobile_change_tracking.revision
        inspector = inspect(c)
        for table in SYNCED:
            columns = {column['name']: column
                       for column in inspector.get_columns(table, schema=schema)}
            for name in ('created_at', 'updated_at'):
                assert columns[name]['nullable'] is False
                default = str(columns[name]['default'] or '')
                assert 'timezone' in default and 'now()' in default
        interaction_columns = {column['name']: column
                               for column in inspector.get_columns('interactions', schema=schema)}
        assert interaction_columns['created_by']['nullable'] is True
        assert interaction_columns['created_by']['default'] is None


def test_backfill_preserves_existing_values_and_never_trusts_activity_logs(legacy):
    """Missing times become the migration time, even where a matching activity entry exists."""
    engine, schema = legacy
    before = _utc_now()
    upgrade(engine, schema)
    after = _utc_now()

    with engine.connect() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        for table in ('clients', 'leads', 'projects', 'contacts'):
            value = c.execute(text(f'SELECT created_at FROM {table} WHERE id = 1')).scalar_one()
            assert value == OLD, table
        for table in ('clients', 'leads', 'projects'):
            # Distinct from created_at, so overwriting it with created_at would fail here.
            value = c.execute(text(f'SELECT updated_at FROM {table} WHERE id = 1')).scalar_one()
            assert value == OLD_UPDATED, table
        # Contacts had created_at but no updated_at: it starts equal to created_at.
        assert c.execute(text('SELECT updated_at FROM contacts WHERE id = 1')).scalar_one() == OLD

        # id 2 has same-tenant, description-matching 'created' entries; ids 4 and 5 have none
        # or a stale one. All of them get the migration time.
        for table in SYNCED:
            for record_id in (2, 4):
                created, updated = c.execute(text(
                    f'SELECT created_at, updated_at FROM {table} WHERE id = :id'), {'id': record_id}).one()
                _assert_recent(created, before, after)
                assert created not in (EARLIEST, LATER, CROSS_TENANT, EDITED), (table, record_id)
                assert updated == created, (table, record_id)
        created, updated = c.execute(text(
            'SELECT created_at, updated_at FROM interactions WHERE id = 5')).one()
        _assert_recent(created, before, after)
        assert updated == created


def test_legacy_interactions_keep_created_by_null(legacy):
    engine, schema = legacy
    upgrade(engine, schema)
    with engine.connect() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        values = c.execute(text('SELECT created_by FROM interactions ORDER BY id')).scalars().all()
        assert values and all(value is None for value in values)


def test_existing_created_by_column_refused_without_changing_history_or_rows(legacy):
    engine, schema = legacy
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        c.execute(text('ALTER TABLE interactions ADD COLUMN created_by integer'))
        c.execute(text('UPDATE interactions SET created_by = 9000 + id'))
        ids_before, columns_before = _ids(c), _columns(c, schema)
        sentinels_before = sorted(c.execute(text(
            'SELECT id, created_by FROM interactions')).all())

    with pytest.raises(RuntimeError, match='Existing interactions.created_by'):
        upgrade(engine, schema)

    with engine.connect() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        assert c.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == \
            previous_revision
        assert _ids(c) == ids_before
        assert _columns(c, schema) == columns_before
        assert sorted(c.execute(text(
            'SELECT id, created_by FROM interactions')).all()) == sentinels_before


def test_cross_tenant_created_by_insert_violates_same_tenant_fk(legacy):
    engine, schema = legacy
    upgrade(engine, schema)
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        # User 2 belongs to tenant 2; raises outside begin_nested so the
        # savepoint rolls back instead of being released after PostgreSQL aborts.
        with pytest.raises(IntegrityError) as denied:
            with c.begin_nested():
                c.execute(text(
                    'INSERT INTO interactions (id, tenant_id, created_by) '
                    'VALUES (:id, 1, :creator)'), {'id': 9001, 'creator': 2})
        assert denied.value.orig.pgcode == '23503'


def test_existing_grants_and_rls_accept_rows_without_new_columns(legacy):
    """Grants and row security are set up BEFORE upgrading, as on a real database."""
    engine, schema = legacy
    role = os.environ['SECURITY_TEST_ROLE']
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        q = c.dialect.identifier_preparer.quote
        if not c.execute(text(
                "SELECT count(*) FROM pg_policies WHERE schemaname=:s "
                "AND tablename='interactions'"), {'s': schema}).scalar_one():
            c.execute(text(f'CREATE POLICY crm_all ON interactions FOR ALL TO PUBLIC '
                           f'USING (tenant_id={TENANT}) WITH CHECK (tenant_id={TENANT})'))
        c.execute(text('ALTER TABLE interactions ENABLE ROW LEVEL SECURITY'))
        c.execute(text('ALTER TABLE interactions FORCE ROW LEVEL SECURITY'))
        c.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO {q(role)}'))
        c.execute(text(f'GRANT SELECT, INSERT, UPDATE ON interactions TO {q(role)}'))

    upgrade(engine, schema)

    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        c.execute(text(f'SET LOCAL ROLE {c.dialect.identifier_preparer.quote(role)}'))
        c.execute(text("SELECT set_config('crm.tenant_id', '1', true)"))
        created, updated = c.execute(text(
            'INSERT INTO interactions (id, tenant_id) VALUES (:id, 1) '
            'RETURNING created_at, updated_at'), {'id': 9999}).one()
        # now() is the transaction start, so the defaults equal it exactly.
        transaction_now = c.execute(text("SELECT timezone('utc', now())")).scalar_one()
        assert created == transaction_now
        assert updated == transaction_now
