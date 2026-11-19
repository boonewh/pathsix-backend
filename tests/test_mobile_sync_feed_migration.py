"""mobile_change_feed: markers, purge records, role safety and downgrade refusal.

The schema is the F-3 tombstone migration chain in PostgreSQL, upgraded one more
revision. This rehearses the exact DDL an operator runs, not a model-built
substitute.
"""
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from migrations.versions import mobile_change_feed
from migrations.versions.mobile_change_feed import (
    down_revision,
    reconcile,
    revision,
    runtime_role_for,
)
from test_mobile_change_tracking_migration import legacy as tracking_legacy, upgrade as tracking_upgrade  # noqa: F401
from test_mobile_stable_ids_migration import legacy as stable_ids_legacy  # noqa: F401
from test_mcp_record_updates_migration import legacy as updates_legacy  # noqa: F401
from test_mcp_record_creation_migration import legacy as creation_legacy  # noqa: F401
from test_mcp_published_scopes_migration import legacy as scopes_legacy  # noqa: F401
from test_ai_connection_idle_migration import legacy as idle_legacy  # noqa: F401
from test_mcp_client_registration_migration import legacy as registration_legacy  # noqa: F401
from test_mcp_lead_migration import legacy as lead_legacy  # noqa: F401
from test_mcp_audit_migration import legacy as audit_legacy  # noqa: F401
from test_oauth_migration import consent_legacy, legacy as oauth_legacy  # noqa: F401
from test_mobile_tombstones_migration import legacy as tombstones_legacy, upgrade as tombstone_upgrade

SYNC_TABLES = ('clients', 'leads', 'projects', 'accounts', 'contacts', 'interactions')
MARKERS = ('sync_change_xid', 'sync_access_xid')
PURGE_COLUMNS = {'id', 'tenant_id', 'entity', 'record_uuid', 'purged_xid', 'purged_at'}
UNSET = object()


@pytest.fixture
def legacy(tombstones_legacy):
    """Schema immediately before mobile_change_feed, at mobile_tombstones."""
    engine, schema = tombstones_legacy
    tombstone_upgrade(engine, schema)
    # The migration must refuse a supplied role that has not been provisioned on
    # the writable tables; grant what the operator's role script grants.
    role = os.environ['SECURITY_TEST_ROLE']
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        quote = c.dialect.identifier_preparer.quote
        c.execute(text(f'GRANT USAGE ON SCHEMA {quote(schema)} TO {quote(role)}'))
        c.execute(text(f'GRANT INSERT ON clients TO {quote(role)}'))
    return engine, schema


def upgrade(engine, schema, role=UNSET):
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        root = Path(mobile_change_feed.__file__).resolve().parents[2]
        cfg = Config(str(root / 'alembic.ini'))
        cfg.set_main_option('script_location', str(root / 'migrations'))
        cfg.attributes.update(connection=c, version_table_schema=schema)
        if role is not UNSET:
            cfg.attributes['runtime_role'] = role
        command.upgrade(cfg, revision)


def _rows(c, tables):
    return {table: [dict(row) for row in
                    c.execute(text(f'SELECT * FROM {table} ORDER BY id')).mappings()]
            for table in tables}


def _ids(c):
    return {table: list(c.execute(text(f'SELECT id FROM {table} ORDER BY id')).scalars())
            for table in SYNC_TABLES}


def _version(c):
    return c.execute(text('SELECT version_num FROM alembic_version')).scalar_one()


def test_revision_follows_tombstones_and_downgrade_refuses():
    assert down_revision == 'mobile_tombstones'
    assert revision == 'mobile_change_feed'
    with pytest.raises(RuntimeError, match='reviewed migration'):
        mobile_change_feed.downgrade()


def test_upgrade_preserves_rows_backfills_markers_and_installs_every_object(legacy):
    engine, schema = legacy
    role = os.environ['SECURITY_TEST_ROLE']
    with engine.connect() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        before_rows = _rows(c, SYNC_TABLES)
        before_columns = {table: sorted(column['name'] for column in
                                        inspect(c).get_columns(table, schema=schema))
                          for table in SYNC_TABLES}

    upgrade(engine, schema, role)

    with engine.connect() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        assert _version(c) == revision
        inspector = inspect(c)
        for table in SYNC_TABLES:
            rows = _rows(c, (table,))[table]
            assert [row['id'] for row in rows] == [row['id'] for row in before_rows[table]]
            assert [{key: row[key] for key in before_columns[table]} for row in rows] == \
                before_rows[table]
            columns = {column['name']: column for column in
                       inspector.get_columns(table, schema=schema)}
            assert sorted(columns) == sorted(before_columns[table] + list(MARKERS)), table
            assert all(columns[name]['nullable'] is False for name in MARKERS)
            changes = [row['sync_change_xid'] for row in rows]
            # One backfill transaction marks every pre-existing row identically.
            assert len(set(changes)) == 1 and changes[0] > 0, table
            assert all(row['sync_access_xid'] == 0 for row in rows), table

            indexes = {index['name']: index for index in inspector.get_indexes(table, schema=schema)}
            for marker in MARKERS:
                index = indexes[f'ix_{table}_tenant_sync_{marker[5:-4]}']
                assert index['column_names'] == ['tenant_id', marker]
            triggers = set(c.execute(text(
                'SELECT t.tgname FROM pg_trigger t JOIN pg_class c ON t.tgrelid=c.oid '
                'JOIN pg_namespace n ON c.relnamespace=n.oid '
                'WHERE n.nspname=:schema AND c.relname=:table AND NOT t.tgisinternal'),
                {'schema': schema, 'table': table}).scalars())
            assert f'sync_mark_{table}' in triggers and f'sync_purge_{table}' in triggers

        functions = set(c.execute(text(
            'SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace '
            'WHERE n.nspname=:schema AND p.proname IN (:mark, :purge)'),
            {'schema': schema, 'mark': 'sync_mark_row',
             'purge': 'sync_record_purge'}).scalars())
        assert functions == {'sync_mark_row', 'sync_record_purge'}
        definition = c.execute(text(
            "SELECT pg_get_functiondef('sync_record_purge()'::regprocedure)")).scalar_one()
        assert 'SECURITY DEFINER' in definition

        purge_columns = {column['name'] for column in
                         inspector.get_columns('sync_purges', schema=schema)}
        assert purge_columns == PURGE_COLUMNS
        purge_indexes = {index['name']: index for index in
                         inspector.get_indexes('sync_purges', schema=schema)}
        assert purge_indexes['ix_sync_purges_tenant_xid']['column_names'] == \
            ['tenant_id', 'purged_xid']

        qualified = f'{c.dialect.identifier_preparer.quote(schema)}.sync_purges'
        assert c.execute(text("SELECT has_table_privilege(:role, :table, 'SELECT')"),
                         {'role': role, 'table': qualified}).scalar_one()
        for privilege in ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER'):
            assert not c.execute(text('SELECT has_table_privilege(:role, :table, :privilege)'),
                                 {'role': role, 'table': qualified,
                                  'privilege': privilege}).scalar_one()
        sequence = c.execute(text('SELECT pg_get_serial_sequence(:table, :column)'),
                             {'table': qualified, 'column': 'id'}).scalar_one()
        assert not c.execute(text(
            "SELECT has_sequence_privilege(:role, :sequence, 'USAGE, UPDATE')"),
            {'role': role, 'sequence': sequence}).scalar_one()
        policy = c.execute(text(
            'SELECT policyname, cmd FROM pg_policies WHERE schemaname=:schema '
            "AND tablename='sync_purges'"), {'schema': schema}).mappings().one()
        assert (policy['policyname'], policy['cmd']) == ('crm_all', 'ALL')
        state = c.execute(text(
            'SELECT relrowsecurity, relforcerowsecurity FROM pg_class c '
            'JOIN pg_namespace n ON n.oid=c.relnamespace '
            "WHERE n.nspname=:schema AND c.relname='sync_purges'"),
            {'schema': schema}).one()
        assert state.relrowsecurity and state.relforcerowsecurity


def test_migrated_triggers_override_markers_and_record_every_delete(legacy):
    engine, schema = legacy
    upgrade(engine, schema, os.environ['SECURITY_TEST_ROLE'])
    purge_uuid = uuid4()
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        inserted = c.execute(text(
            'INSERT INTO clients (id, tenant_id, name, sync_change_xid, sync_access_xid) '
            "VALUES (9001, 1, 'After migration', 111, 222) RETURNING uuid, "
            'sync_change_xid, sync_access_xid')).mappings().one()
        assert inserted['sync_change_xid'] not in (111, 222)
        assert inserted['sync_change_xid'] > 0 and inserted['sync_access_xid'] == 0
        purge_uuid = inserted['uuid']
        c.execute(text('DELETE FROM clients WHERE id=9001'))
        purged = c.execute(text(
            'SELECT tenant_id, entity, record_uuid, purged_xid FROM sync_purges')).mappings().one()
    assert purged['tenant_id'] == 1 and purged['entity'] == 'clients'
    assert purged['record_uuid'] == purge_uuid
    assert purged['purged_xid'] > 0


@pytest.mark.parametrize('existing', ['re_run', 'table', 'marker', 'function'])
def test_reconcile_refuses_existing_state_without_changing_rows(legacy, existing):
    engine, schema = legacy
    role = os.environ['SECURITY_TEST_ROLE']
    if existing == 're_run':
        upgrade(engine, schema, role)
        pattern = 'Existing sync_purges table'
        expected_version = revision
    else:
        pattern = {'table': 'Existing sync_purges table',
                   'marker': 'Existing clients sync markers',
                   'function': 'Existing sync functions'}[existing]
        expected_version = down_revision
    with engine.connect() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        before = _ids(c)

    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        if existing == 'table':
            c.execute(text('CREATE TABLE sync_purges (sentinel integer)'))
        elif existing == 'marker':
            c.execute(text('ALTER TABLE clients ADD COLUMN sync_change_xid bigint'))
        elif existing == 'function':
            c.execute(text('CREATE FUNCTION sync_mark_row() RETURNS trigger LANGUAGE plpgsql AS '
                           '$$ BEGIN RETURN NEW; END $$'))
        with pytest.raises(RuntimeError, match=pattern):
            reconcile(c, schema, role)

    with engine.connect() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        assert _version(c) == expected_version
        assert _ids(c) == before
        assert ('sync_purges' in inspect(c).get_table_names(schema=schema)) == \
            (existing in ('re_run', 'table'))


def test_runtime_role_is_derived_when_none_is_supplied(legacy):
    engine, schema = legacy
    role = os.environ['SECURITY_TEST_ROLE']
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        assert runtime_role_for(c, schema, role) == role
        assert runtime_role_for(c, schema) == role

    upgrade(engine, schema)  # no runtime_role attribute: the documented alembic upgrade head

    with engine.connect() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        assert _version(c) == revision
        assert c.execute(text('SELECT has_table_privilege(:role, :table, \'SELECT\')'),
                         {'role': role, 'table': f'"{schema}".sync_purges'}).scalar_one()


def test_runtime_role_selection_refuses_unsafe_ambiguous_and_ungranted_roles(legacy):
    engine, schema = legacy
    role = os.environ['SECURITY_TEST_ROLE']
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        quote = c.dialect.identifier_preparer.quote
        extra = 'sync_feed_test_' + uuid4().hex[:12]
        other = 'sync_feed_other_' + uuid4().hex[:12]
        unsafe = 'sync_feed_unsafe_' + uuid4().hex[:12]
        try:
            c.execute(text(f'CREATE ROLE {quote(extra)} NOSUPERUSER NOCREATEDB NOCREATEROLE '
                           'NOBYPASSRLS'))
            c.execute(text(f'CREATE ROLE {quote(other)} NOSUPERUSER NOCREATEDB NOCREATEROLE '
                           'NOBYPASSRLS'))
            c.execute(text(f'CREATE ROLE {quote(unsafe)} NOSUPERUSER CREATEDB NOCREATEROLE '
                           'NOBYPASSRLS'))
            c.execute(text(f'GRANT INSERT ON clients TO {quote(extra)}'))
            c.execute(text(f'GRANT INSERT ON clients TO {quote(unsafe)}'))

            with pytest.raises(RuntimeError, match='single runtime role'):
                runtime_role_for(c, schema)
            with pytest.raises(RuntimeError, match='not the role granted on clients'):
                runtime_role_for(c, schema, other)
            with pytest.raises(RuntimeError, match='restricted runtime role'):
                runtime_role_for(c, schema, unsafe)
        finally:
            c.execute(text(f'REVOKE INSERT ON clients FROM {quote(extra)}'))
            c.execute(text(f'REVOKE INSERT ON clients FROM {quote(unsafe)}'))
            for name in (extra, other, unsafe):
                c.execute(text(f'DROP ROLE IF EXISTS {quote(name)}'))

        c.execute(text(f'REVOKE INSERT ON clients FROM {quote(role)}'))
        with pytest.raises(RuntimeError, match='not the role granted on clients'):
            runtime_role_for(c, schema, role)
        with pytest.raises(RuntimeError, match='single runtime role'):
            runtime_role_for(c, schema)

    with engine.connect() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        assert _version(c) == down_revision
        assert 'sync_purges' not in inspect(c).get_table_names(schema=schema)


def test_reconcile_refuses_replication_runtime_role(legacy):
    engine, schema = legacy
    role = os.environ['SECURITY_TEST_ROLE']
    unsafe = 'sync_feed_replication_' + uuid4().hex[:12]
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        quote = c.dialect.identifier_preparer.quote
        try:
            c.execute(text(f'CREATE ROLE {quote(unsafe)} NOSUPERUSER NOCREATEDB NOCREATEROLE '
                           'NOBYPASSRLS REPLICATION'))
            c.execute(text(f'GRANT INSERT ON clients TO {quote(unsafe)}'))
            with pytest.raises(RuntimeError, match='restricted runtime role'):
                reconcile(c, schema, unsafe)
            assert _version(c) == down_revision
            assert 'sync_purges' not in inspect(c).get_table_names(schema=schema)
        finally:
            c.execute(text(f'REVOKE INSERT ON clients FROM {quote(unsafe)}'))
            c.execute(text(f'DROP ROLE IF EXISTS {quote(unsafe)}'))


def test_reconcile_refuses_writable_assumable_role(legacy):
    engine, schema = legacy
    role = os.environ['SECURITY_TEST_ROLE']
    writer = 'sync_feed_writer_' + uuid4().hex[:12]
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        quote = c.dialect.identifier_preparer.quote
        owner = c.execute(text('SELECT current_user')).scalar_one()
        defaults = (f'ALTER DEFAULT PRIVILEGES FOR ROLE {quote(owner)} '
                    f'IN SCHEMA {quote(schema)} ')
        try:
            c.execute(text(f'CREATE ROLE {quote(writer)} NOSUPERUSER NOCREATEDB NOCREATEROLE '
                           'NOBYPASSRLS NOREPLICATION'))
            c.execute(text(f'GRANT {quote(writer)} TO {quote(role)}'))
            # The migration-created ledger inherits INSERT for this role, so the
            # runtime could write it after SET ROLE even though its own grant is SELECT.
            c.execute(text(defaults + f'GRANT INSERT ON TABLES TO {quote(writer)}'))
            with pytest.raises(RuntimeError, match='must be SELECT only'):
                with c.begin_nested():
                    reconcile(c, schema, role)
            assert _version(c) == down_revision
            assert 'sync_purges' not in inspect(c).get_table_names(schema=schema)
        finally:
            c.execute(text(defaults + f'REVOKE INSERT ON TABLES FROM {quote(writer)}'))
            c.execute(text(f'REVOKE {quote(writer)} FROM {quote(role)}'))
            c.execute(text(f'DROP ROLE IF EXISTS {quote(writer)}'))
