"""Tombstone DDL, the live-account-number rule and runtime access in real PostgreSQL.

The schema is built the same way as the change-tracking migration test: walk the
existing migration chain to the revision immediately before mobile_tombstones,
then add the unique rule on accounts.account_number that production had.
"""
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from migrations.versions import mobile_tombstones
from migrations.versions.mobile_tombstones import LIVE_NUMBER_INDEX, down_revision, revision
from test_mobile_change_tracking_migration import legacy as tracking_legacy, upgrade as tracking_upgrade
from test_mobile_stable_ids_migration import legacy as stable_ids_legacy  # noqa: F401
from test_mcp_record_updates_migration import legacy as updates_legacy  # noqa: F401
from test_mcp_record_creation_migration import legacy as creation_legacy  # noqa: F401
from test_mcp_published_scopes_migration import legacy as scopes_legacy  # noqa: F401
from test_ai_connection_idle_migration import legacy as idle_legacy  # noqa: F401
from test_mcp_client_registration_migration import legacy as registration_legacy  # noqa: F401
from test_mcp_lead_migration import legacy as lead_legacy  # noqa: F401
from test_mcp_audit_migration import legacy as audit_legacy  # noqa: F401
from test_oauth_migration import consent_legacy, legacy as oauth_legacy  # noqa: F401

# Independent of the migration's own list: dropping a table there must fail here.
TABLES = ('contacts', 'accounts', 'interactions')
TENANT = "NULLIF(current_setting('crm.tenant_id', true), '')::integer"
LEGACY_NUMBER_RULE = 'uq_accounts_account_number'


@pytest.fixture
def legacy(tracking_legacy):
    """Schema immediately before mobile_tombstones, with production's unique rule."""
    engine, schema = tracking_legacy
    tracking_upgrade(engine, schema)
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        # The chain above builds a minimal accounts table; production had this
        # column and a database-wide UNIQUE constraint on it.
        c.execute(text('ALTER TABLE accounts ADD COLUMN account_number text'))
        c.execute(text(f'ALTER TABLE accounts ADD CONSTRAINT {LEGACY_NUMBER_RULE} '
                       'UNIQUE (account_number)'))
    return engine, schema


def upgrade(engine, schema):
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        root = Path(mobile_tombstones.__file__).resolve().parents[2]
        cfg = Config(str(root / 'alembic.ini'))
        cfg.set_main_option('script_location', str(root / 'migrations'))
        cfg.attributes.update(connection=c, version_table_schema=schema,
                              runtime_role=os.environ['SECURITY_TEST_ROLE'])
        command.upgrade(cfg, revision)


def _columns(c, schema):
    return {table: sorted(column['name'] for column in inspect(c).get_columns(table, schema=schema))
            for table in TABLES}


def _rows(c):
    return {table: sorted(c.execute(text(f'SELECT id FROM {table}')).scalars())
            for table in TABLES}


def _snapshot(c, schema):
    return (_columns(c, schema), _rows(c), c.execute(text(
        'SELECT version_num FROM alembic_version')).scalar_one())


def _assert_unchanged(engine, schema, before):
    with engine.connect() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        assert _snapshot(c, schema) == before


def test_upgrade_adds_nullable_tombstones_both_foreign_keys_and_live_number_index(legacy):
    engine, schema = legacy
    upgrade(engine, schema)
    with engine.connect() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        assert c.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == revision
        inspector = inspect(c)
        for table in TABLES:
            columns = {column['name']: column
                       for column in inspector.get_columns(table, schema=schema)}
            for name in ('deleted_at', 'deleted_by'):
                assert columns[name]['nullable'] is True, (table, name)
            foreign_keys = {fk['name']: fk
                            for fk in inspector.get_foreign_keys(table, schema=schema)}
            single = foreign_keys[f'{table}_deleted_by_fkey']
            assert single['constrained_columns'] == ['deleted_by']
            assert (single['referred_table'], single['referred_columns']) == ('users', ['id'])
            same_tenant = foreign_keys[f'fk_{table}_deleted_by_same_tenant']
            assert same_tenant['constrained_columns'] == ['tenant_id', 'deleted_by']
            assert (same_tenant['referred_table'], same_tenant['referred_columns']) == \
                ('users', ['tenant_id', 'id'])
            # Existing rows are all live: nothing is backfilled.
            assert c.execute(text(
                f'SELECT count(*) FROM {table} '
                'WHERE deleted_at IS NOT NULL OR deleted_by IS NOT NULL')).scalar_one() == 0

        indexes = {index['name']: index for index in inspector.get_indexes('accounts', schema=schema)}
        live = indexes[LIVE_NUMBER_INDEX]
        assert live['unique'] is True and live['column_names'] == ['account_number']
        assert 'deleted_at IS NULL' in str(
            live['dialect_options'].get('postgresql_where'))
        constraints = {item['name'] for item in
                       inspector.get_unique_constraints('accounts', schema=schema)}
        assert LEGACY_NUMBER_RULE not in constraints
        assert all(item['name'] != LEGACY_NUMBER_RULE for item in indexes.values())


def test_cross_tenant_deleted_by_is_rejected_and_same_tenant_is_accepted(legacy):
    engine, schema = legacy
    upgrade(engine, schema)
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        # User 2 belongs to tenant 2; contact 1 belongs to tenant 1.
        with pytest.raises(IntegrityError) as denied:
            with c.begin_nested():
                c.execute(text(
                    'UPDATE contacts SET deleted_at = now(), deleted_by = 2 WHERE id = 1'))
        assert denied.value.orig.pgcode == '23503'

        c.execute(text('UPDATE contacts SET deleted_at = now(), deleted_by = 3 WHERE id = 1'))
        assert c.execute(text(
            'SELECT deleted_by FROM contacts WHERE id = 1')).scalar_one() == 3


def test_existing_tombstone_column_aborts_without_changing_history_or_rows(legacy):
    engine, schema = legacy
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        c.execute(text('ALTER TABLE interactions ADD COLUMN deleted_at timestamp'))
        c.execute(text("UPDATE interactions SET deleted_at = '2020-01-01' WHERE id = 1"))
        before = _snapshot(c, schema)

    with pytest.raises(RuntimeError, match='Existing interactions tombstone columns'):
        upgrade(engine, schema)

    _assert_unchanged(engine, schema, before)


def test_a_standalone_unique_index_on_account_number_is_accepted(legacy):
    """Preflight must accept either shape of the pre-existing rule, not just a constraint."""
    engine, schema = legacy
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        c.execute(text(f'ALTER TABLE accounts DROP CONSTRAINT {LEGACY_NUMBER_RULE}'))
        c.execute(text('CREATE UNIQUE INDEX accounts_number_unique '
                       'ON accounts (account_number)'))

    upgrade(engine, schema)

    with engine.connect() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        assert c.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == revision
        indexes = {index['name']: index
                   for index in inspect(c).get_indexes('accounts', schema=schema)}
        assert 'accounts_number_unique' not in indexes
        assert indexes[LIVE_NUMBER_INDEX]['unique'] is True
        assert 'deleted_at IS NULL' in str(
            indexes[LIVE_NUMBER_INDEX]['dialect_options'].get('postgresql_where'))


@pytest.mark.parametrize('rule', ['missing', 'duplicate', 'partial', 'reserved_name'])
def test_preflight_refuses_a_missing_duplicate_partial_or_reserved_unique_rule(legacy, rule):
    engine, schema = legacy
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        if rule == 'missing':
            c.execute(text(f'ALTER TABLE accounts DROP CONSTRAINT {LEGACY_NUMBER_RULE}'))
        elif rule == 'duplicate':
            c.execute(text('CREATE UNIQUE INDEX accounts_number_duplicate '
                           'ON accounts (account_number)'))
        elif rule == 'partial':
            c.execute(text(f'ALTER TABLE accounts DROP CONSTRAINT {LEGACY_NUMBER_RULE}'))
            c.execute(text('CREATE UNIQUE INDEX accounts_number_partial '
                           'ON accounts (account_number) WHERE account_number IS NOT NULL'))
        else:
            c.execute(text(f'ALTER TABLE accounts DROP CONSTRAINT {LEGACY_NUMBER_RULE}'))
            c.execute(text(f'CREATE UNIQUE INDEX {LIVE_NUMBER_INDEX} '
                           'ON accounts (account_number)'))
        before = _snapshot(c, schema)

    with pytest.raises(RuntimeError, match='Expected exactly one unconditional unique rule'):
        upgrade(engine, schema)

    _assert_unchanged(engine, schema, before)


def test_account_numbers_are_unique_among_live_rows_only_on_the_migrated_schema(legacy):
    engine, schema = legacy
    upgrade(engine, schema)
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        c.execute(text("INSERT INTO accounts (id, tenant_id, account_number) "
                       "VALUES (900, 1, 'A-1')"))
    with pytest.raises(IntegrityError) as denied:
        with engine.begin() as c:
            c.execute(text(f'SET LOCAL search_path="{schema}"'))
            c.execute(text("INSERT INTO accounts (id, tenant_id, account_number) "
                           "VALUES (901, 1, 'A-1')"))
    assert denied.value.orig.pgcode == '23505'

    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        c.execute(text('UPDATE accounts SET deleted_at = now(), deleted_by = 1 WHERE id = 900'))
        c.execute(text("INSERT INTO accounts (id, tenant_id, account_number) "
                       "VALUES (901, 1, 'A-1')"))

    with pytest.raises(IntegrityError) as denied:
        with engine.begin() as c:
            c.execute(text(f'SET LOCAL search_path="{schema}"'))
            c.execute(text("INSERT INTO accounts (id, tenant_id, account_number) "
                           "VALUES (902, 1, 'A-1')"))
    assert denied.value.orig.pgcode == '23505'


def test_existing_grants_and_rls_accept_the_new_columns(legacy):
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
        deleted_by, deleted_at = c.execute(text(
            'INSERT INTO interactions (id, tenant_id, deleted_by, deleted_at) '
            'VALUES (9999, 1, 1, now()) RETURNING deleted_by, deleted_at')).one()
        assert deleted_by == 1 and deleted_at is not None
        assert c.execute(text(
            'SELECT deleted_by FROM interactions WHERE id = 9999')).scalar_one() == 1
        # The composite foreign key still refuses a cross-tenant actor.
        with pytest.raises(IntegrityError) as denied:
            with c.begin_nested():
                c.execute(text(
                    'INSERT INTO interactions (id, tenant_id, deleted_by) '
                    'VALUES (9998, 1, 2)'))
        assert denied.value.orig.pgcode == '23503'


def test_revision_follows_change_tracking_and_refuses_to_downgrade():
    assert down_revision == 'mobile_change_tracking'
    with pytest.raises(RuntimeError, match='reviewed migration'):
        mobile_tombstones.downgrade()
