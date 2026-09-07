import os
from pathlib import Path
import uuid

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError, DBAPIError

from migrations.versions.tenant_membership_indexes import TABLES, revision, down_revision, reconcile


def config(connection=None, schema=None):
    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / 'alembic.ini'))
    cfg.set_main_option('script_location', str(root / 'migrations'))
    cfg.attributes['connection'] = connection
    cfg.attributes['version_table_schema'] = schema
    return cfg


def test_membership_revision_merges_all_legacy_heads():
    assert ScriptDirectory.from_config(config()).get_heads() == [revision]
    assert set(down_revision) == {'add_project_assigned_to', 'add_subscriptions_table', 'add_tenants_table'}


def test_models_declare_all_tenant_membership_foreign_keys():
    from app.database import Base
    from app import models  # noqa: F401
    tables = {t.name: t for t in Base.metadata.tables.values() if 'tenant_id' in t.c}
    assert set(tables) == set(TABLES)
    for table in tables.values():
        assert not table.c.tenant_id.nullable
        assert any(fk.target_fullname == 'tenants.id' for fk in table.c.tenant_id.foreign_keys)


@pytest.fixture
def legacy():
    url = os.getenv('SECURITY_TEST_DATABASE_URL')
    if not url:
        pytest.skip('Requires PostgreSQL migration validation')
    engine = create_engine(url, hide_parameters=True)
    schema = 'security_test_' + uuid.uuid4().hex
    try:
        with engine.begin() as c:
            c.execute(text(f'CREATE SCHEMA "{schema}"'))
            c.execute(text(f'SET LOCAL search_path="{schema}"'))
            c.execute(text('CREATE TABLE tenants (id integer PRIMARY KEY)'))
            c.execute(text('INSERT INTO tenants VALUES (1), (2)'))
            for table in TABLES:
                c.execute(text(f'CREATE TABLE {table} (id integer PRIMARY KEY, tenant_id integer NOT NULL)'))
                c.execute(text(f'INSERT INTO {table} VALUES (1, 1)'))
            c.execute(text('ALTER TABLE users ADD CONSTRAINT existing_user_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(id)'))
            c.execute(text('CREATE INDEX existing_user_tenant_index ON users(tenant_id)'))
            c.execute(text('CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY)'))
            for head in down_revision:
                c.execute(text('INSERT INTO alembic_version VALUES (:head)'), {'head': head})
        yield engine, schema
    finally:
        with engine.begin() as c:
            c.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


def upgrade(engine, schema):
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        command.upgrade(config(c, schema), revision)


def test_membership_upgrade_preserves_rows_and_denies_orphans(legacy):
    engine, schema = legacy
    upgrade(engine, schema)
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        assert c.execute(text('SELECT version_num FROM alembic_version')).scalars().all() == [revision]
        before = {}
        for table in TABLES:
            assert c.execute(text(f'SELECT count(*) FROM {table}')).scalar_one() == 1
            assert len(inspect(c).get_foreign_keys(table, schema=schema)) == 1
            before[table] = inspect(c).get_indexes(table, schema=schema)
            assert any(i['column_names'][:1] == ['tenant_id'] for i in before[table])
        assert not c.execute(text('SELECT count(*) FROM pg_constraint WHERE connamespace=CAST(:schema AS regnamespace) AND contype=\'f\' AND NOT convalidated'), {'schema': schema}).scalar_one()
        reconcile(c, schema)
        assert before == {t: inspect(c).get_indexes(t, schema=schema) for t in TABLES}
        role = os.getenv('SECURITY_TEST_ROLE')
        if role:
            quoted = c.dialect.identifier_preparer.quote(role)
            c.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO {quoted}'))
            c.execute(text(f'GRANT SELECT, INSERT, UPDATE, DELETE ON clients, tenants TO {quoted}'))
            c.execute(text(f'SET LOCAL ROLE {quoted}'))
        c.execute(text('INSERT INTO clients VALUES (2, 2)'))
        for statement in ('INSERT INTO clients VALUES (3, 999)',
                          'UPDATE clients SET tenant_id=999 WHERE id=1',
                          'DELETE FROM tenants WHERE id=1'):
            with c.begin_nested() as savepoint:
                with pytest.raises(IntegrityError) as exc:
                    c.execute(text(statement))
                assert exc.value.orig.pgcode == '23503'
                savepoint.rollback()


def test_membership_orphan_preflight_leaves_history_and_schema_unchanged(legacy):
    engine, schema = legacy
    with engine.begin() as c:
        c.execute(text(f'INSERT INTO "{schema}".clients VALUES (2, 999)'))
    with pytest.raises(RuntimeError, match='invalid tenant references'):
        upgrade(engine, schema)
    with engine.connect() as c:
        assert inspect(c).get_indexes('accounts', schema=schema) == []
        assert inspect(c).get_foreign_keys('accounts', schema=schema) == []
        assert set(c.execute(text(f'SELECT version_num FROM "{schema}".alembic_version')).scalars()) == set(down_revision)


def test_membership_name_collision_rolls_back_prior_ddl(legacy):
    engine, schema = legacy
    with engine.begin() as c:
        c.execute(text(f'CREATE INDEX ix_clients_tenant_membership ON "{schema}".clients(id)'))
    with pytest.raises(DBAPIError):
        upgrade(engine, schema)
    with engine.connect() as c:
        assert inspect(c).get_indexes('accounts', schema=schema) == []
        assert inspect(c).get_foreign_keys('accounts', schema=schema) == []
        assert set(c.execute(text(f'SELECT version_num FROM "{schema}".alembic_version')).scalars()) == set(down_revision)


def test_membership_partial_index_does_not_mask_missing_full_index(legacy):
    engine, schema = legacy
    with engine.begin() as c:
        c.execute(text(f'CREATE INDEX partial_tenant ON "{schema}".clients(tenant_id) WHERE id>10'))
    upgrade(engine, schema)
    with engine.connect() as c:
        assert {i['name'] for i in inspect(c).get_indexes('clients', schema=schema)} == {'partial_tenant', 'ix_clients_tenant_membership'}


def test_membership_missing_table_refuses_partial_migration(legacy):
    engine, schema = legacy
    with engine.begin() as c:
        c.execute(text(f'DROP TABLE "{schema}".subscriptions'))
    with pytest.raises(RuntimeError, match='tables are missing'):
        upgrade(engine, schema)
    with engine.connect() as c:
        assert inspect(c).get_indexes('accounts', schema=schema) == []
