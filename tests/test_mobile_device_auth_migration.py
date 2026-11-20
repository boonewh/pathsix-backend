"""mobile_device_auth: devices table, row security, grants and downgrade refusal.

The schema is the F-5 migration chain in PostgreSQL, upgraded one more revision.
This rehearses the exact DDL an operator runs, not a model-built substitute.
"""
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from migrations.versions import mobile_device_auth
from migrations.versions.mobile_device_auth import (
    down_revision,
    reconcile,
    revision,
    runtime_role_for,
    secure,
    secure_holdings,
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
from test_mobile_sync_feed_migration import upgrade as sync_upgrade  # noqa: F401
from test_mobile_idempotent_writes_migration import legacy as receipts_legacy, upgrade as receipts_upgrade  # noqa: F401

TABLE = 'devices'
HOLDINGS = 'device_holdings'
REQUIRED_COLUMNS = {
    'id', 'tenant_id', 'user_id', 'name', 'platform', 'created_at', 'last_seen_at',
    'refresh_hash', 'previous_refresh_hash', 'previous_valid_until', 'refreshed_at', 'refresh_expires_at',
    'credential_marker', 'revoked_at', 'revoked_by',
    'sync_generation', 'sync_acked_seq', 'sync_pending_seq', 'sync_pending_removals',
}
UNSET = object()


@pytest.fixture
def legacy(receipts_legacy):
    """Schema immediately before mobile_device_auth, at mobile_idempotent_writes."""
    engine, schema = receipts_legacy
    receipts_upgrade(engine, schema, os.environ['SECURITY_TEST_ROLE'])
    return engine, schema


def upgrade(engine, schema, role=UNSET):
    with engine.begin() as connection:
        connection.execute(text(f'SET LOCAL search_path="{schema}"'))
        root = Path(mobile_device_auth.__file__).resolve().parents[2]
        cfg = Config(str(root / 'alembic.ini'))
        cfg.set_main_option('script_location', str(root / 'migrations'))
        cfg.attributes.update(connection=connection, version_table_schema=schema)
        if role is not UNSET:
            cfg.attributes['runtime_role'] = role
        command.upgrade(cfg, revision)


def version(connection):
    return connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one()


def table_exists(connection, schema, table=TABLE):
    return table in inspect(connection).get_table_names(schema=schema)


def test_revision_follows_idempotent_writes_and_downgrade_refuses():
    assert down_revision == 'mobile_idempotent_writes'
    assert revision == 'mobile_device_auth'
    with pytest.raises(RuntimeError, match='reviewed migration'):
        mobile_device_auth.downgrade()


def test_upgrade_installs_table_fks_indexes_grants_and_rls(legacy):
    engine, schema = legacy
    role = os.environ['SECURITY_TEST_ROLE']
    upgrade(engine, schema, role)

    with engine.connect() as connection:
        connection.execute(text(f'SET LOCAL search_path="{schema}"'))
        assert version(connection) == revision
        inspector = inspect(connection)
        columns = {column['name']: column
                   for column in inspector.get_columns(TABLE, schema=schema)}
        assert REQUIRED_COLUMNS <= set(columns)
        assert {'previous_refresh_hash', 'revoked_at', 'revoked_by'} <= set(columns)
        assert not columns['id']['nullable']
        assert columns['previous_valid_until']['nullable']
        assert 'TIMESTAMP' in str(columns['previous_valid_until']['type']).upper()

        foreign_keys = inspector.get_foreign_keys(TABLE, schema=schema)
        assert any(fk['constrained_columns'] == ['tenant_id', 'user_id']
                   and fk['referred_table'] == 'users'
                   and fk['referred_columns'] == ['tenant_id', 'id']
                   and fk['name'] == 'fk_devices_user_same_tenant'
                   for fk in foreign_keys)
        assert any(fk['constrained_columns'] == ['tenant_id']
                   and fk['referred_table'] == 'tenants' for fk in foreign_keys)

        indexes = {index['name']: index for index in inspector.get_indexes(TABLE, schema=schema)}
        assert indexes['ix_devices_tenant_user']['column_names'] == ['tenant_id', 'user_id']

        quote = connection.dialect.identifier_preparer.quote
        qualified = f'{quote(schema)}.{quote(TABLE)}'
        for privilege in ('SELECT', 'INSERT', 'UPDATE'):
            assert connection.execute(text(
                'SELECT has_table_privilege(:role, :table, :privilege)'),
                {'role': role, 'table': qualified, 'privilege': privilege}).scalar_one()
        for privilege in ('DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER'):
            assert not connection.execute(text(
                'SELECT has_table_privilege(:role, :table, :privilege)'),
                {'role': role, 'table': qualified, 'privilege': privilege}).scalar_one()

        policies = connection.execute(text(
            'SELECT policyname, cmd, qual, with_check '
            'FROM pg_policies WHERE schemaname=:schema AND tablename=:table'),
            {'schema': schema, 'table': TABLE}).mappings().all()
        by_name = {policy['policyname']: policy for policy in policies}
        assert set(by_name) == {'crm_select', 'crm_modify'}
        assert by_name['crm_select']['cmd'] == 'SELECT'
        select_expr = by_name['crm_select']['qual']
        for setting in ('tenant_id', 'auth_user_id', 'auth_tenant_id'):
            assert setting in select_expr
        assert 'user_id' in select_expr
        assert by_name['crm_modify']['cmd'] == 'ALL'
        for expression in (by_name['crm_modify']['qual'], by_name['crm_modify']['with_check']):
            assert expression and 'tenant_id' in expression

        state = connection.execute(text(
            'SELECT relrowsecurity, relforcerowsecurity FROM pg_class c '
            'JOIN pg_namespace n ON n.oid=c.relnamespace '
            "WHERE n.nspname=:schema AND c.relname=:table"),
            {'schema': schema, 'table': TABLE}).one()
        assert state.relrowsecurity and state.relforcerowsecurity

        # Part B: what a device holds is written by the feed and cleared on ack
        # or revocation, so the runtime role can delete but never update rows.
        holding_columns = {column['name'] for column in inspector.get_columns(HOLDINGS, schema=schema)}
        assert holding_columns == {'device_id', 'tenant_id', 'entity', 'record_uuid'}
        holding_pk = inspector.get_pk_constraint(HOLDINGS, schema=schema)
        assert set(holding_pk['constrained_columns']) == {'device_id', 'entity', 'record_uuid'}
        assert any(fk['constrained_columns'] == ['tenant_id', 'device_id']
                   and fk['referred_table'] == TABLE
                   and fk['referred_columns'] == ['tenant_id', 'id']
                   and fk['name'] == 'fk_device_holdings_device'
                   for fk in inspector.get_foreign_keys(HOLDINGS, schema=schema))
        holdings_qualified = f'{quote(schema)}.{quote(HOLDINGS)}'
        for privilege in ('SELECT', 'INSERT', 'DELETE'):
            assert connection.execute(text(
                'SELECT has_table_privilege(:role, :table, :privilege)'),
                {'role': role, 'table': holdings_qualified, 'privilege': privilege}).scalar_one()
        for privilege in ('UPDATE', 'TRUNCATE', 'REFERENCES', 'TRIGGER'):
            assert not connection.execute(text(
                'SELECT has_table_privilege(:role, :table, :privilege)'),
                {'role': role, 'table': holdings_qualified, 'privilege': privilege}).scalar_one()
        holding_policies = connection.execute(text(
            'SELECT policyname, cmd FROM pg_policies '
            'WHERE schemaname=:schema AND tablename=:table'),
            {'schema': schema, 'table': HOLDINGS}).mappings().all()
        assert [(row['policyname'], row['cmd']) for row in holding_policies] == [('crm_all', 'ALL')]
        holding_state = connection.execute(text(
            'SELECT relrowsecurity, relforcerowsecurity FROM pg_class c '
            'JOIN pg_namespace n ON n.oid=c.relnamespace '
            "WHERE n.nspname=:schema AND c.relname=:table"),
            {'schema': schema, 'table': HOLDINGS}).one()
        assert holding_state.relrowsecurity and holding_state.relforcerowsecurity


@pytest.mark.parametrize('existing', ['re_run', 'table', 'holdings'])
def test_reconcile_refuses_existing_state_without_changing_rows(legacy, existing):
    engine, schema = legacy
    role = os.environ['SECURITY_TEST_ROLE']
    if existing == 're_run':
        upgrade(engine, schema, role)
        expected_version = revision
    else:
        expected_version = down_revision

    with engine.connect() as connection:
        connection.execute(text(f'SET LOCAL search_path="{schema}"'))
        before_version = version(connection)

    with engine.begin() as connection:
        connection.execute(text(f'SET LOCAL search_path="{schema}"'))
        if existing == 'table':
            connection.execute(text('CREATE TABLE devices (sentinel integer)'))
        if existing == 'holdings':
            connection.execute(text('CREATE TABLE device_holdings (sentinel integer)'))
        with pytest.raises(RuntimeError, match='Existing devices tables'):
            reconcile(connection, schema, role)

    with engine.connect() as connection:
        connection.execute(text(f'SET LOCAL search_path="{schema}"'))
        assert version(connection) == expected_version
        assert before_version == (revision if existing == 're_run' else down_revision)
        assert table_exists(connection, schema, HOLDINGS) == (existing in ('re_run', 'holdings'))
        assert table_exists(connection, schema, TABLE) == (existing != 'holdings')


def test_secure_refuses_to_reapply_policies(legacy):
    engine, schema = legacy
    role = os.environ['SECURITY_TEST_ROLE']
    upgrade(engine, schema, role)

    with engine.begin() as connection:
        connection.execute(text(f'SET LOCAL search_path="{schema}"'))
        with pytest.raises(RuntimeError, match='Existing devices policies'):
            secure(connection, schema, role)


def test_secure_holdings_refuses_to_reapply_policies(legacy):
    engine, schema = legacy
    role = os.environ['SECURITY_TEST_ROLE']
    upgrade(engine, schema, role)

    with engine.begin() as connection:
        connection.execute(text(f'SET LOCAL search_path="{schema}"'))
        with pytest.raises(RuntimeError, match='Existing device_holdings policies'):
            secure_holdings(connection, schema, role)


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
    unsafe = 'f6_unsafe_' + uuid4().hex[:12]
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
            assert not table_exists(connection, schema, HOLDINGS)
        finally:
            connection.execute(text(f'REVOKE INSERT ON clients FROM {quote(unsafe)}'))
            connection.execute(text(f'DROP ROLE IF EXISTS {quote(unsafe)}'))
