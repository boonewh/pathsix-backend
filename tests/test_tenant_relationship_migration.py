import os
from pathlib import Path
import uuid

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from migrations.versions.tenant_relationships import EDGES, revision, down_revision, reconcile


def config(connection=None, schema=None):
    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / 'alembic.ini'))
    cfg.set_main_option('script_location', str(root / 'migrations'))
    cfg.attributes.update(connection=connection, version_table_schema=schema)
    return cfg


def test_relationship_inventory_covers_declared_tenant_foreign_keys():
    from app.database import Base
    from app import models  # noqa: F401
    expected = set()
    for table in Base.metadata.tables.values():
        if 'tenant_id' not in table.c:
            continue
        for fk in table.foreign_keys:
            if fk.parent.name != 'tenant_id' and 'tenant_id' in fk.column.table.c:
                expected.add((table.name, fk.parent.name, fk.column.table.name))
    assert set(EDGES) == expected
    assert ScriptDirectory.from_config(config()).get_heads() == [revision]


@pytest.fixture
def legacy():
    url = os.getenv('SECURITY_TEST_DATABASE_URL')
    if not url:
        pytest.skip('Requires PostgreSQL relationship validation')
    engine = create_engine(url, hide_parameters=True)
    schema = 'security_test_' + uuid.uuid4().hex
    columns = {}
    for child, column, parent in EDGES:
        columns.setdefault(child, set()).add(column)
        columns.setdefault(parent, set())
    try:
        with engine.begin() as c:
            c.execute(text(f'CREATE SCHEMA "{schema}"'))
            c.execute(text(f'SET LOCAL search_path="{schema}"'))
            for table, fields in columns.items():
                extra = ''.join(f', {column} integer' for column in sorted(fields))
                c.execute(text(f'CREATE TABLE {table} (id integer PRIMARY KEY, tenant_id integer NOT NULL{extra})'))
                c.execute(text(f'INSERT INTO {table} (id, tenant_id) VALUES (1, 1), (2, 2)'))
            c.execute(text('CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY)'))
            c.execute(text('INSERT INTO alembic_version VALUES (:head)'), {'head': down_revision})
        yield engine, schema
    finally:
        with engine.begin() as c:
            c.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


def upgrade(engine, schema):
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        command.upgrade(config(c, schema), revision)


def test_every_relationship_rejects_cross_tenant_insert_and_update(legacy):
    engine, schema = legacy
    upgrade(engine, schema)
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        assert c.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == revision
        before = {t: inspect(c).get_foreign_keys(t, schema=schema) for t in {e[0] for e in EDGES}}
        assert sum(len(v) for v in before.values()) == len(EDGES)
        reconcile(c, schema)
        assert before == {t: inspect(c).get_foreign_keys(t, schema=schema) for t in before}
        role = os.getenv('SECURITY_TEST_ROLE')
        if role:
            qrole = c.dialect.identifier_preparer.quote(role)
            c.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO {qrole}'))
            # Synthetic schema only: exercise all constraints even on platform-hidden tables.
            c.execute(text(f'GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA "{schema}" TO {qrole}'))
            c.execute(text(f'SET LOCAL ROLE {qrole}'))
        for child, column, _ in EDGES:
            for statement in (f'INSERT INTO {child} (id, tenant_id, {column}) VALUES (3, 1, 2)',
                              f'UPDATE {child} SET {column}=2 WHERE id=1'):
                with c.begin_nested() as sp:
                    with pytest.raises(IntegrityError) as exc:
                        c.execute(text(statement))
                    assert exc.value.orig.pgcode == '23503', (child, column)
                    sp.rollback()
            c.execute(text(f'UPDATE {child} SET {column}=1 WHERE id=1'))
            # Optional NULLs keep their existing semantics (not MATCH FULL).
            with c.begin_nested() as sp:
                c.execute(text(f'UPDATE {child} SET {column}=NULL WHERE id=1'))
                sp.rollback()
        # Changing a referenced parent's company must not invalidate its children.
        with c.begin_nested() as sp:
            with pytest.raises(IntegrityError) as exc:
                c.execute(text('UPDATE users SET tenant_id=2 WHERE id=1'))
            assert exc.value.orig.pgcode == '23503'
            sp.rollback()


@pytest.mark.parametrize('parent_id', [2, 999])
def test_relationship_preflight_preserves_invalid_data_for_review(legacy, parent_id):
    engine, schema = legacy
    with engine.begin() as c:
        c.execute(text(f'UPDATE "{schema}".clients SET assigned_to=:parent WHERE id=1'), {'parent': parent_id})
    with pytest.raises(RuntimeError, match='invalid relationships'):
        upgrade(engine, schema)
    with engine.connect() as c:
        assert inspect(c).get_unique_constraints('users', schema=schema) == []
        assert inspect(c).get_foreign_keys('accounts', schema=schema) == []
        assert c.execute(text(f'SELECT assigned_to FROM "{schema}".clients WHERE id=1')).scalar_one() == parent_id
        assert c.execute(text(f'SELECT version_num FROM "{schema}".alembic_version')).scalar_one() == down_revision


def test_relationship_ddl_failure_rolls_back_every_constraint(legacy):
    engine, schema = legacy
    with engine.begin() as c:
        c.execute(text(f'ALTER TABLE "{schema}".projects ADD CONSTRAINT fk_projects_assigned_to_same_tenant CHECK (true)'))
    with pytest.raises(DBAPIError):
        upgrade(engine, schema)
    with engine.connect() as c:
        assert inspect(c).get_unique_constraints('users', schema=schema) == []
        assert inspect(c).get_foreign_keys('accounts', schema=schema) == []
        assert c.execute(text(f'SELECT version_num FROM "{schema}".alembic_version')).scalar_one() == down_revision
