"""mobile_idempotent_writes: receipts table, role safety and downgrade refusal.

The schema is the F-4 change-feed migration chain in PostgreSQL, upgraded one
more revision. This rehearses the exact DDL an operator runs, not a model-built
substitute.
"""
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from migrations.versions import mobile_idempotent_writes
from migrations.versions.mobile_idempotent_writes import (
    down_revision,
    reconcile,
    revision,
    runtime_role_for,
    secure,
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
from test_mobile_tombstones_migration import legacy as tombstones_legacy, upgrade as tombstone_upgrade  # noqa: F401
from test_mobile_sync_feed_migration import legacy as sync_legacy  # noqa: F401
from test_mobile_sync_feed_migration import upgrade as sync_upgrade

TABLE = 'write_receipts'
COLUMNS = {'id', 'tenant_id', 'user_id', 'request_key', 'endpoint',
           'input_hash', 'status_code', 'response', 'created_at'}
UNSET = object()


@pytest.fixture
def legacy(sync_legacy):
    """Schema immediately before mobile_idempotent_writes, at mobile_change_feed."""
    engine, schema = sync_legacy
    sync_upgrade(engine, schema)
    return engine, schema


def upgrade(engine, schema, role=UNSET):
    with engine.begin() as connection:
        connection.execute(text(f'SET LOCAL search_path="{schema}"'))
        root = Path(mobile_idempotent_writes.__file__).resolve().parents[2]
        cfg = Config(str(root / 'alembic.ini'))
        cfg.set_main_option('script_location', str(root / 'migrations'))
        cfg.attributes.update(connection=connection, version_table_schema=schema)
        if role is not UNSET:
            cfg.attributes['runtime_role'] = role
        command.upgrade(cfg, revision)


def version(connection):
    return connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one()


def table_exists(connection, schema):
    return TABLE in inspect(connection).get_table_names(schema=schema)


def test_revision_follows_change_feed_and_downgrade_refuses():
    assert down_revision == 'mobile_change_feed'
    assert revision == 'mobile_idempotent_writes'
    with pytest.raises(RuntimeError, match='reviewed migration'):
        mobile_idempotent_writes.downgrade()


def test_upgrade_installs_table_index_constraints_grants_and_rls(legacy):
    engine, schema = legacy
    role = os.environ['SECURITY_TEST_ROLE']
    upgrade(engine, schema, role)

    with engine.connect() as connection:
        connection.execute(text(f'SET LOCAL search_path="{schema}"'))
        assert version(connection) == revision
        inspector = inspect(connection)
        columns = {column['name']: column
                   for column in inspector.get_columns(TABLE, schema=schema)}
        assert set(columns) == COLUMNS
        assert all(not column['nullable'] for column in columns.values())

        uniques = [constraint for constraint in inspector.get_unique_constraints(
            TABLE, schema=schema) if constraint['name'] == 'uq_write_receipts_key']
        assert len(uniques) == 1
        assert set(uniques[0]['column_names']) == {'tenant_id', 'user_id', 'request_key'}

        indexes = {index['name']: index for index in inspector.get_indexes(TABLE, schema=schema)}
        assert indexes['ix_write_receipts_created']['column_names'] == ['created_at']

        quote = connection.dialect.identifier_preparer.quote
        qualified = f'{quote(schema)}.{quote(TABLE)}'
        assert connection.execute(text(
            "SELECT has_table_privilege(:role, :table, 'SELECT') AND "
            "has_table_privilege(:role, :table, 'INSERT')"),
            {'role': role, 'table': qualified}).scalar_one()
        for privilege in ('UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER'):
            assert not connection.execute(text(
                'SELECT has_table_privilege(:role, :table, :privilege)'),
                {'role': role, 'table': qualified, 'privilege': privilege}).scalar_one()
        sequence = connection.execute(text('SELECT pg_get_serial_sequence(:table, :column)'),
                                      {'table': qualified, 'column': 'id'}).scalar_one()
        assert connection.execute(text("SELECT has_sequence_privilege(:role, :sequence, 'USAGE')"),
                                  {'role': role, 'sequence': sequence}).scalar_one()
        assert not connection.execute(text(
            "SELECT has_sequence_privilege(:role, :sequence, 'UPDATE')"),
            {'role': role, 'sequence': sequence}).scalar_one()

        policies = connection.execute(text(
            'SELECT policyname, cmd, qual, with_check '
            'FROM pg_policies WHERE schemaname=:schema AND tablename=:table'),
            {'schema': schema, 'table': TABLE}).mappings().all()
        assert len(policies) == 1
        assert (policies[0]['policyname'], policies[0]['cmd']) == ('crm_all', 'ALL')
        for expression in (policies[0]['qual'], policies[0]['with_check']):
            assert 'tenant_id' in expression and 'user_id' in expression
        state = connection.execute(text(
            'SELECT relrowsecurity, relforcerowsecurity FROM pg_class c '
            'JOIN pg_namespace n ON n.oid=c.relnamespace '
            "WHERE n.nspname=:schema AND c.relname=:table"),
            {'schema': schema, 'table': TABLE}).one()
        assert state.relrowsecurity and state.relforcerowsecurity


@pytest.mark.parametrize('existing', ['re_run', 'table'])
def test_reconcile_refuses_existing_state_without_changing_rows(legacy, existing):
    engine, schema = legacy
    role = os.environ['SECURITY_TEST_ROLE']
    if existing == 're_run':
        upgrade(engine, schema, role)
        expected_version = revision
    else:
        expected_version = down_revision
    pattern = 'Existing write_receipts table'

    with engine.connect() as connection:
        connection.execute(text(f'SET LOCAL search_path="{schema}"'))
        before_version = version(connection)

    with engine.begin() as connection:
        connection.execute(text(f'SET LOCAL search_path="{schema}"'))
        if existing == 'table':
            connection.execute(text('CREATE TABLE write_receipts (sentinel integer)'))
        with pytest.raises(RuntimeError, match=pattern):
            reconcile(connection, schema, role)

    with engine.connect() as connection:
        connection.execute(text(f'SET LOCAL search_path="{schema}"'))
        assert version(connection) == expected_version
        assert before_version == (revision if existing == 're_run' else down_revision)
        assert table_exists(connection, schema)


def test_secure_refuses_to_reapply_policies(legacy):
    engine, schema = legacy
    role = os.environ['SECURITY_TEST_ROLE']
    upgrade(engine, schema, role)

    with engine.begin() as connection:
        connection.execute(text(f'SET LOCAL search_path="{schema}"'))
        with pytest.raises(RuntimeError, match='Existing write_receipts policies'):
            secure(connection, schema, role)


def test_runtime_role_is_derived_when_none_is_supplied(legacy):
    engine, schema = legacy
    role = os.environ['SECURITY_TEST_ROLE']
    with engine.begin() as connection:
        connection.execute(text(f'SET LOCAL search_path="{schema}"'))
        assert runtime_role_for(connection, schema, role) == role
        assert runtime_role_for(connection, schema) == role

    upgrade(engine, schema)

    with engine.connect() as connection:
        connection.execute(text(f'SET LOCAL search_path="{schema}"'))
        assert version(connection) == revision
        quote = connection.dialect.identifier_preparer.quote
        assert connection.execute(text(
            "SELECT has_table_privilege(:role, :table, 'SELECT')"),
            {'role': role, 'table': f'{quote(schema)}.{quote(TABLE)}'}).scalar_one()


def test_reconcile_refuses_an_unsafe_runtime_role(legacy):
    engine, schema = legacy
    role = os.environ['SECURITY_TEST_ROLE']
    unsafe = 'f5_unsafe_' + uuid4().hex[:12]
    with engine.begin() as connection:
        connection.execute(text(f'SET LOCAL search_path="{schema}"'))
        quote = connection.dialect.identifier_preparer.quote
        try:
            connection.execute(text(
                f'CREATE ROLE {quote(unsafe)} NOSUPERUSER CREATEDB NOCREATEROLE NOBYPASSRLS'))
            connection.execute(text(f'GRANT INSERT ON clients TO {quote(unsafe)}'))
            with pytest.raises(RuntimeError, match='restricted runtime role'):
                reconcile(connection, schema, unsafe)
            assert version(connection) == down_revision
            assert not table_exists(connection, schema)
        finally:
            connection.execute(text(f'REVOKE INSERT ON clients FROM {quote(unsafe)}'))
            connection.execute(text(f'DROP ROLE IF EXISTS {quote(unsafe)}'))
