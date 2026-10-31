"""Sliding inactivity expiry for AI connections within their fixed absolute limit."""
from alembic import op
from sqlalchemy import text, inspect

revision = 'ai_connection_idle_expiry'
down_revision = 'mcp_client_registration'
branch_labels = depends_on = None


def reconcile(connection, schema):
    if connection.dialect.name != 'postgresql':
        raise RuntimeError('PostgreSQL required')
    connection.execute(text("SET LOCAL lock_timeout='5s'"))
    connection.execute(text("SET LOCAL statement_timeout='60s'"))
    if 'active_until' in {c['name'] for c in inspect(connection).get_columns('ai_connections', schema=schema)}:
        raise RuntimeError('Existing inactivity column requires review')
    q = connection.dialect.identifier_preparer.quote
    table = f'{q(schema)}.{q("ai_connections")}'
    # expires_at stays the immutable absolute limit. active_until is the only
    # lifetime that use extends, and it can never pass expires_at. Existing
    # grants keep exactly the lifetime they were approved with.
    connection.execute(text(f'ALTER TABLE {table} ADD COLUMN active_until timestamp'))
    connection.execute(text(f'UPDATE {table} SET active_until = expires_at'))
    connection.execute(text(f'''ALTER TABLE {table} ALTER COLUMN active_until SET NOT NULL,
        ADD CONSTRAINT ck_ai_connections_active_until CHECK (active_until > created_at AND active_until <= expires_at)'''))


def upgrade():
    connection = op.get_bind()
    reconcile(connection, connection.execute(text('SELECT current_schema()')).scalar_one())


def downgrade():
    raise RuntimeError('Preserve consent lifetimes; use a reviewed migration')
