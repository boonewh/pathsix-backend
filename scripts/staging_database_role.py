"""Provision a separate restricted runtime login; never print connection secrets.

    python scripts/staging_database_role.py                     # staging (DATABASE_URL)
    python scripts/staging_database_role.py --target mobile     # mobile (DATABASE_URL)
    <admin-url> | python scripts/staging_database_role.py --target rehearsal|production

Targets are fixed in database_targets.py. The runtime URL is handed off in a
mode-0600 file on the machine, never printed.
"""
import argparse
import json
import os
import secrets
import sys
from pathlib import Path
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.engine import make_url
from psycopg2 import sql

sys.path.insert(0, str(Path(__file__).resolve().parent))
from database_targets import checked_admin_url, target  # noqa: E402

ROLE = 'pathsix_crm_staging_runtime'
WRITABLE_TABLES = ('accounts', 'activity_logs', 'clients', 'contacts', 'files',
    'interactions', 'leads', 'projects', 'subscriptions', 'user_preferences',
    'user_roles', 'users')
READ_ONLY_TABLES = ('tenants', 'roles')
CREDENTIAL_PATH = '/tmp/pathsix-{target}-runtime-credentials.json'


def grant_runtime_access(connection, role, schema='public'):
    """Explicit grants only; no default grants for future tables or platform data."""
    quote = connection.dialect.identifier_preparer.quote
    role_sql, schema_sql = quote(role), quote(schema)
    connection.execute(text(f'GRANT USAGE ON SCHEMA {schema_sql} TO {role_sql}'))
    for table in WRITABLE_TABLES + READ_ONLY_TABLES:
        table_sql = f'{schema_sql}.{quote(table)}'
        privileges = 'SELECT, INSERT, UPDATE, DELETE' if table in WRITABLE_TABLES else 'SELECT'
        connection.execute(text(f'GRANT {privileges} ON TABLE {table_sql} TO {role_sql}'))
        if table in WRITABLE_TABLES:
            columns = {c['name'] for c in inspect(connection).get_columns(table, schema=schema)}
            if 'id' in columns:
                sequence = connection.execute(text('SELECT pg_get_serial_sequence(:table, :column)'),
                    {'table': table_sql, 'column': 'id'}).scalar_one()
                if sequence:
                    # Sequence name is returned by PostgreSQL, fully identifier-quoted.
                    connection.execute(text(f'GRANT USAGE, SELECT ON SEQUENCE {sequence} TO {role_sql}'))


def env_url(name):
    """The app's own DATABASE_URL, accepted only where it is that target's admin login."""
    spec = target(name)
    url = make_url(os.environ['DATABASE_URL'].replace('postgres://', 'postgresql://', 1))
    if (os.getenv('FLY_APP_NAME') != spec['app'] or url.host not in spec['hosts'] or
            url.database != spec['database'] or url.username != spec['admin_user']):
        raise RuntimeError('Refusing a target other than ' + name)
    return url


# Staging and mobile still hold an administrator DATABASE_URL at provisioning
# time, so the login comes from the environment. Rehearsal and production run
# as the restricted role already and take the administrator URL on stdin.
FROM_ENVIRONMENT = ('staging', 'mobile')


def target_url(name):
    if name in FROM_ENVIRONMENT:
        return env_url(name)
    return checked_admin_url(name, sys.stdin.readline(), os.getenv('FLY_APP_NAME'))


def provision(name='staging'):
    ROLE = target(name)['runtime_role']
    credential_path = CREDENTIAL_PATH.format(target=name)
    url = target_url(name)
    if Path(credential_path).exists():
        raise RuntimeError('Credential handoff already exists; do not overwrite')
    engine = create_engine(url, hide_parameters=True)
    password = secrets.token_urlsafe(48)
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL lock_timeout = '5s'"))
        if connection.execute(text('SELECT 1 FROM pg_roles WHERE rolname=:role'), {'role': ROLE}).scalar():
            raise RuntimeError('Role already exists; refusing to replace credentials')
        # The old administrator is retained. No ownership or existing credentials change.
        with connection.connection.cursor() as cursor:
            cursor.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD {}').format(sql.Identifier(ROLE), sql.Literal(password)))
        quote = connection.dialect.identifier_preparer.quote
        connection.execute(text(f'GRANT CONNECT ON DATABASE {quote(url.database)} TO {quote(ROLE)}'))
        # PUBLIC privileges would otherwise undermine the restricted role.
        connection.execute(text('REVOKE CREATE ON SCHEMA public FROM PUBLIC'))
        grant_runtime_access(connection, ROLE)
    runtime_url = url.set(username=ROLE, password=password)
    runtime = create_engine(runtime_url, hide_parameters=True)
    with runtime.connect() as connection:
        row = connection.execute(text('SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls FROM pg_roles WHERE rolname=current_user')).one()
        if any(row):
            raise RuntimeError('Runtime role attributes are unsafe')
        if connection.execute(text("SELECT has_schema_privilege(current_user,'public','CREATE') OR has_database_privilege(current_user,current_database(),'CREATE')")).scalar_one():
            raise RuntimeError('Runtime login can create database objects')
        for table in WRITABLE_TABLES:
            if not all(connection.execute(text('SELECT has_table_privilege(current_user,:table,:privileges)'), {'table': 'public.' + table, 'privileges': privilege}).scalar_one() for privilege in ('SELECT', 'INSERT', 'UPDATE', 'DELETE')):
                raise RuntimeError('Missing runtime privileges')
        for table in ('backups', 'backup_restores', 'alembic_version', 'chat_messages'):
            if connection.execute(text('SELECT has_table_privilege(current_user,:table,:privileges)'), {'table': 'public.' + table, 'privileges': 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE'}).scalar_one():
                raise RuntimeError('Unexpected platform table privilege')
        print('Restricted login connection and privilege preflight passed')
    runtime.dispose()
    fd = os.open(credential_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as output:
        json.dump({'previous_url': url.render_as_string(hide_password=False),
                   'runtime_url': runtime_url.render_as_string(hide_password=False)}, output)
    engine.dispose()
    print('Credential handoff prepared in a mode-0600 temporary file; values not printed')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--target', choices=['staging', 'mobile', 'rehearsal', 'production'], default='staging')
    try:
        provision(parser.parse_args().target)
    except Exception as exc:
        # Never expose a connection URL or credential-bearing SQL in tracebacks.
        print('Runtime role provisioning failed: ' + type(exc).__name__, file=sys.stderr)
        raise SystemExit(1)
