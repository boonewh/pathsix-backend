"""The contacts migration copies every person into contacts without losing or duplicating anyone."""
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from migrations.versions import contacts_unified
from migrations.versions.contacts_unified import revision, down_revision


@pytest.fixture
def legacy():
    url = os.getenv('SECURITY_TEST_DATABASE_URL')
    if not url or not os.getenv('SECURITY_TEST_ROLE'):
        pytest.skip('Requires PostgreSQL restricted role')
    engine = create_engine(url, hide_parameters=True)
    schema = 'security_test_' + uuid4().hex
    person = ('contact_person varchar(100), contact_title varchar(100), email varchar(120), phone varchar(20), '
              'phone_label varchar(20), secondary_phone varchar(20), secondary_phone_label varchar(20)')
    with engine.begin() as c:
        c.execute(text(f'CREATE SCHEMA "{schema}"'))
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        c.execute(text('CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY)'))
        c.execute(text('INSERT INTO alembic_version VALUES (:head)'), {'head': down_revision})
        c.execute(text(f'CREATE TABLE leads (id integer PRIMARY KEY, tenant_id integer NOT NULL, name varchar(100), '
                       f'deleted_at timestamp, {person}, UNIQUE (tenant_id, id))'))
        c.execute(text(f'CREATE TABLE clients (id integer PRIMARY KEY, tenant_id integer NOT NULL, name varchar(100), '
                       f'deleted_at timestamp, source_lead_id integer REFERENCES leads(id), {person}, '
                       f'UNIQUE (tenant_id, id))'))
        c.execute(text('CREATE TABLE projects (id integer PRIMARY KEY, tenant_id integer NOT NULL, '
                       'client_id integer REFERENCES clients(id), lead_id integer REFERENCES leads(id), '
                       'primary_contact_name varchar(100), primary_contact_title varchar(100), '
                       'primary_contact_email varchar(120), primary_contact_phone varchar(20), '
                       'primary_contact_phone_label varchar(20), UNIQUE (tenant_id, id))'))
        c.execute(text('CREATE TABLE contacts (id serial PRIMARY KEY, tenant_id integer NOT NULL, '
                       'client_id integer REFERENCES clients(id), lead_id integer REFERENCES leads(id), '
                       'first_name varchar(100), last_name varchar(100), title varchar(100), email varchar(120), '
                       'phone varchar(20), phone_label varchar(20), secondary_phone varchar(20), '
                       'secondary_phone_label varchar(20), notes text, created_at timestamp, '
                       'CONSTRAINT ck_contacts_one_parent CHECK (num_nonnulls(client_id, lead_id) = 1))'))
        c.execute(text("""
            INSERT INTO leads (id, tenant_id, name, deleted_at, contact_person, email, phone, phone_label) VALUES
              (1, 1, 'Open lead', NULL, ' Ada  Lovelace ', 'ada@ai.example.com', '+15550100001', 'mobile'),
              (2, 1, 'Converted lead', now(), 'Grace Hopper', NULL, NULL, NULL),
              (3, 1, 'No person', NULL, NULL, '', '', 'work'),
              (4, 1, 'Converted, not deleted', NULL, NULL, NULL, NULL, NULL);
            INSERT INTO clients (id, tenant_id, name, source_lead_id, contact_person, contact_title, email) VALUES
              (1, 1, 'ACME', NULL, 'Jane Doe', 'Owner', 'jane@acme.example.com'),
              (2, 1, 'Grace Co', 2, 'Grace Hopper', NULL, 'grace@co.example.com'),
              (3, 1, 'Email only', NULL, NULL, NULL, 'info@email.example.com'),
              (4, 1, 'Still a lead too', 4, NULL, NULL, NULL);
            INSERT INTO contacts (tenant_id, client_id, lead_id, first_name, last_name, title, email) VALUES
              (1, 1, NULL, 'JANE', 'DOE', NULL, NULL),
              (1, NULL, 2, 'Extra', 'Person', NULL, NULL),
              (1, NULL, 2, 'Grace', 'Hopper', 'Admiral', NULL),
              (1, NULL, 4, 'Stays', 'Put', NULL, NULL);
            INSERT INTO projects (id, tenant_id, client_id, lead_id, primary_contact_name, primary_contact_email,
                                  primary_contact_phone) VALUES
              (1, 1, 1, NULL, 'Jane Doe', 'JANE@ACME.EXAMPLE.COM', NULL),
              (2, 1, 1, NULL, 'Ray Site', 'ray@acme.example.com', '+15550100002'),
              (3, 1, NULL, NULL, 'Pat Lee', NULL, NULL),
              (4, 1, NULL, NULL, NULL, NULL, NULL);
        """))
    try:
        yield engine, schema
    finally:
        with engine.begin() as c:
            c.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


def upgrade(engine, schema):
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        root = Path(contacts_unified.__file__).resolve().parents[2]
        cfg = Config(str(root / 'alembic.ini'))
        cfg.set_main_option('script_location', str(root / 'migrations'))
        cfg.attributes.update(connection=c, version_table_schema=schema, runtime_role=os.environ['SECURITY_TEST_ROLE'])
        command.upgrade(cfg, revision)


def rows(c, sql):
    return [tuple(r) for r in c.execute(text(sql)).all()]


def test_every_person_is_copied_once_and_projects_point_at_them(legacy):
    engine, schema = legacy
    upgrade(engine, schema)
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        assert c.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == revision
        people = rows(c, 'SELECT client_id, lead_id, project_id, is_primary, first_name, last_name, title, email, '
                         'phone, phone_label FROM contacts ORDER BY client_id NULLS LAST, lead_id NULLS LAST, '
                         'project_id NULLS LAST, id')
        assert people == [
            # ACME's existing "JANE DOE" was promoted, gaps filled, not duplicated.
            (1, None, None, True, 'JANE', 'DOE', 'Owner', 'jane@acme.example.com', None, None),
            # Project 2's person joined ACME's contacts (not primary).
            (1, None, None, False, 'Ray', 'Site', None, 'ray@acme.example.com', '+15550100002', 'work'),
            # The converted, deleted lead's people moved; its matching person became primary.
            (2, None, None, False, 'Extra', 'Person', None, None, None, None),
            (2, None, None, True, 'Grace', 'Hopper', 'Admiral', 'grace@co.example.com', None, None),
            (3, None, None, True, None, None, None, 'info@email.example.com', None, None),
            (None, 1, None, True, 'Ada', 'Lovelace', None, 'ada@ai.example.com', '+15550100001', 'mobile'),
            (None, 2, None, True, 'Grace', 'Hopper', None, None, None, None),
            # A converted lead that was never deleted keeps its people.
            (None, 4, None, False, 'Stays', 'Put', None, None, None, None),
            (None, None, 3, True, 'Pat', 'Lee', None, None, None, None),
        ]
        links = dict(rows(c, 'SELECT p.id, c.first_name FROM projects p LEFT JOIN contacts c '
                             'ON c.id = p.primary_contact_id ORDER BY p.id'))
        assert links == {1: 'JANE', 2: 'Ray', 3: 'Pat', 4: None}
        # Old columns are untouched backups.
        assert rows(c, 'SELECT contact_person FROM clients WHERE id = 1') == [('Jane Doe',)]
        # Database rules: one parent, one primary per record, same-tenant links.
        with pytest.raises(IntegrityError):
            with c.begin_nested():
                c.execute(text('INSERT INTO contacts (tenant_id, client_id, is_primary) VALUES (1, 1, true)'))
        with pytest.raises(IntegrityError):
            with c.begin_nested():
                c.execute(text('INSERT INTO contacts (tenant_id, client_id, project_id) VALUES (1, 1, 3)'))
        with pytest.raises(IntegrityError):
            with c.begin_nested():
                c.execute(text('INSERT INTO contacts (tenant_id, project_id) VALUES (2, 3)'))
        # Deleting a person unlinks projects without touching their tenant.
        c.execute(text("DELETE FROM contacts WHERE first_name = 'Ray'"))
        assert rows(c, 'SELECT tenant_id, primary_contact_id FROM projects WHERE id = 2') == [(1, None)]


def test_migration_is_atomic_when_it_fails(legacy):
    engine, schema = legacy
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        c.execute(text('ALTER TABLE contacts DROP CONSTRAINT ck_contacts_one_parent'))
    with pytest.raises(Exception):
        upgrade(engine, schema)
    with engine.connect() as c:
        c.execute(text(f'SET LOCAL search_path="{schema}"'))
        assert c.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == down_revision
        columns = {r[0] for r in c.execute(text("SELECT column_name FROM information_schema.columns "
                                                "WHERE table_schema=:s AND table_name='contacts'"), {'s': schema})}
        assert 'is_primary' not in columns and 'project_id' not in columns
