"""mobile_record_refs: per-tenant refs, grants, row security and downgrade refusal.

The schema is the F-14 migration chain in PostgreSQL, upgraded one more revision.
This rehearses the exact DDL an operator runs, not a model-built substitute.
"""
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from migrations.versions import mobile_record_refs
from migrations.versions.mobile_record_refs import (
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
from test_mobile_sync_feed_migration import legacy as sync_legacy, upgrade as sync_upgrade  # noqa: F401
from test_mobile_idempotent_writes_migration import legacy as receipts_legacy, upgrade as receipts_upgrade  # noqa: F401
from test_mobile_device_auth_migration import legacy as device_auth_legacy  # noqa: F401
from test_mobile_device_auth_migration import upgrade as device_upgrade

TABLE = 'mobile_record_refs'
COLUMNS = {'id', 'tenant_id', 'ref', 'entity', 'record_id', 'created_at'}
UNSET = object()

pytestmark = pytest.mark.skipif(
    not os.getenv('SECURITY_TEST_DATABASE_URL') or not os.getenv('SECURITY_TEST_ROLE'),
    reason='Requires PostgreSQL and the restricted runtime role',
)


@pytest.fixture
def legacy(device_auth_legacy):
    """Schema immediately before mobile_record_refs, at mobile_device_auth."""
    engine, schema = device_auth_legacy
    device_upgrade(engine, schema, os.environ['SECURITY_TEST_ROLE'])
    return engine, schema


def upgrade(engine, schema, role=UNSET):
    with engine.begin() as connection:
        connection.execute(text(f'SET LOCAL search_path="{schema}"'))
        root = Path(mobile_record_refs.__file__).resolve().parents[2]
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


def test_revision_follows_device_auth_and_downgrade_refuses():
    assert down_revision == 'mobile_device_auth'
    assert revision == 'mobile_record_refs'
    with pytest.raises(RuntimeError, match='reviewed migration'):
        mobile_record_refs.downgrade()


def test_upgrade_installs_table_constraints_index_grants_and_rls(legacy):
    engine, schema = legacy
    role = os.environ['SECURITY_TEST_ROLE']
    upgrade(engine, schema, role)

    with engine.connect() as connection:
        connection.execute(text(f'SET LOCAL search_path="{schema}"'))
        assert version(connection) == revision
        inspector = inspect(connection)
        columns = {column['name']: column for column in inspector.get_columns(TABLE, schema=schema)}
        assert set(columns) == COLUMNS
        assert all(not column['nullable'] for column in columns.values())
        assert set(inspector.get_pk_constraint(TABLE, schema=schema)['constrained_columns']) == {'id'}

        foreign_keys = inspector.get_foreign_keys(TABLE, schema=schema)
        assert any(fk['constrained_columns'] == ['tenant_id'] and fk['referred_table'] == 'tenants'
                   and fk['referred_columns'] == ['id'] for fk in foreign_keys)

        uniques = [constraint for constraint in inspector.get_unique_constraints(TABLE, schema=schema)
                   if constraint['name'] == 'uq_mobile_record_refs_ref']
        assert len(uniques) == 1
        assert set(uniques[0]['column_names']) == {'tenant_id', 'ref'}

        indexes = {index['name']: index for index in inspector.get_indexes(TABLE, schema=schema)}
        assert indexes['ix_mobile_record_refs_created']['column_names'] == ['created_at']

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
        assert not connection.execute(text("SELECT has_sequence_privilege(:role, :sequence, 'UPDATE')"),
                                      {'role': role, 'sequence': sequence}).scalar_one()

        policies = connection.execute(text(
            'SELECT policyname, cmd, qual, with_check FROM pg_policies '
            'WHERE schemaname=:schema AND tablename=:table'),
            {'schema': schema, 'table': TABLE}).mappings().all()
        assert len(policies) == 1
        assert (policies[0]['policyname'], policies[0]['cmd']) == ('crm_all', 'ALL')
        for expression in (policies[0]['qual'], policies[0]['with_check']):
            assert expression and 'tenant_id' in expression and 'current_setting' in expression

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

    with engine.connect() as connection:
        connection.execute(text(f'SET LOCAL search_path="{schema}"'))
        before_version = version(connection)

    with engine.begin() as connection:
        connection.execute(text(f'SET LOCAL search_path="{schema}"'))
        if existing == 'table':
            connection.execute(text(f'CREATE TABLE {TABLE} (sentinel integer)'))
        with pytest.raises(RuntimeError, match='Existing mobile_record_refs table'):
            reconcile(connection, schema, role)

    with engine.connect() as connection:
        connection.execute(text(f'SET LOCAL search_path="{schema}"'))
        assert version(connection) == expected_version
        assert before_version == expected_version
        assert table_exists(connection, schema)


def test_secure_refuses_to_reapply_policies(legacy):
    engine, schema = legacy
    role = os.environ['SECURITY_TEST_ROLE']
    upgrade(engine, schema, role)

    with engine.begin() as connection:
        connection.execute(text(f'SET LOCAL search_path="{schema}"'))
        with pytest.raises(RuntimeError, match='Existing mobile_record_refs policies'):
            secure(connection, schema, role)


def test_runtime_role_is_derived_when_none_is_supplied(legacy):
    engine, schema = legacy
    role = os.environ['SECURITY_TEST_ROLE']
    with engine.begin() as connection:
        connection.execute(text(f'SET LOCAL search_path="{schema}"'))
        assert runtime_role_for(connection, schema, role) == role


def test_runtime_role_sees_only_its_tenant_and_refs_are_unique_per_tenant(legacy):
    engine, schema = legacy
    role = os.environ['SECURITY_TEST_ROLE']
    upgrade(engine, schema, role)
    shared = '2a5d6d4e-7c66-4a70-9d82-13319fc5129b'

    with engine.begin() as connection:
        quote = connection.dialect.identifier_preparer.quote
        connection.execute(text(f'SET LOCAL ROLE {quote(role)}'))
        connection.execute(text(f'SET LOCAL search_path="{schema}"'))
        for tenant in (1, 2):
            connection.execute(text("SELECT set_config('crm.tenant_id', :tenant, true)"),
                               {'tenant': str(tenant)})
            connection.execute(text(
                f'INSERT INTO {TABLE} (tenant_id, ref, entity, record_id, created_at) '
                'VALUES (:tenant, :ref, \'clients\', :record_id, now())'),
                {'tenant': tenant, 'ref': shared, 'record_id': tenant})

        connection.execute(text("SELECT set_config('crm.tenant_id', '1', true)"))
        assert connection.execute(text(f'SELECT tenant_id FROM {TABLE}')).scalars().all() == [1]
        connection.execute(text("SELECT set_config('crm.tenant_id', '2', true)"))
        assert connection.execute(text(f'SELECT tenant_id FROM {TABLE}')).scalars().all() == [2]

        connection.execute(text("SELECT set_config('crm.tenant_id', '1', true)"))
        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(text(
                    f'INSERT INTO {TABLE} (tenant_id, ref, entity, record_id, created_at) '
                    'VALUES (1, :ref, \'clients\', 99, now())'), {'ref': shared})
