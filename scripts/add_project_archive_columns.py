"""Additive archive schema, safe to apply before either application release.

Run with a database administrator connection. No Projects are archived by this
script. Existing multi-head Alembic histories are deliberately left unchanged.
"""
from sqlalchemy import create_engine, inspect, text
import os


def apply(connection):
    if connection.dialect.name != 'postgresql':
        raise RuntimeError('This deployment migration requires PostgreSQL')
    connection.execute(text("SET LOCAL lock_timeout='5s'"))
    connection.execute(text("SET LOCAL statement_timeout='60s'"))
    connection.execute(text('ALTER TABLE projects ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP WITHOUT TIME ZONE'))
    connection.execute(text('ALTER TABLE projects ADD COLUMN IF NOT EXISTS archived_by INTEGER'))
    connection.execute(text('ALTER TABLE projects ADD COLUMN IF NOT EXISTS archive_history JSON'))
    connection.execute(text('CREATE INDEX IF NOT EXISTS ix_projects_archived_at ON projects (archived_at)'))
    cols = {c['name']: str(c['type']) for c in inspect(connection).get_columns('projects')}
    if not (cols['archived_at'].startswith('TIMESTAMP') and cols['archived_by'] == 'INTEGER' and cols['archive_history'] == 'JSON'):
        raise RuntimeError('Unexpected archive schema; migration rolled back')


if __name__ == '__main__':
    url = os.environ['DATABASE_URL'].replace('postgres://', 'postgresql://', 1)
    with create_engine(url, hide_parameters=True).begin() as connection:
        apply(connection)
    print('Archive columns installed. No business records changed.')
