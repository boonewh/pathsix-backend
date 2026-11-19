"""Stable UUID identifiers for syncable CRM records (mobile Phase 0, item 1).

Integer primary keys stay the web CRM's identifiers. The UUID is the identifier
an offline device can generate and reconcile against.
"""
from alembic import op
from sqlalchemy import text, inspect

revision = 'mobile_stable_ids'
down_revision = 'mcp_record_updates'
branch_labels = depends_on = None

TABLES = ('clients', 'leads', 'projects', 'contacts', 'interactions', 'accounts')


def reconcile(connection, schema):
    if connection.dialect.name != 'postgresql':
        raise RuntimeError('PostgreSQL required')
    connection.execute(text("SET LOCAL lock_timeout='5s'"))
    connection.execute(text("SET LOCAL statement_timeout='60s'"))
    q = connection.dialect.identifier_preparer.quote
    inspector = inspect(connection)
    for name in TABLES:
        if 'uuid' in {c['name'] for c in inspector.get_columns(name, schema=schema)}:
            raise RuntimeError(f'Existing uuid column on {name} requires review')
    for name in TABLES:
        table = f'{q(schema)}.{q(name)}'
        # A volatile default on ADD COLUMN evaluates per row, so every existing
        # row receives its own value in this statement. The default stays for
        # writers that do not supply one; devices will supply their own.
        connection.execute(text(f'ALTER TABLE {table} ADD COLUMN uuid uuid NOT NULL DEFAULT gen_random_uuid()'))
        connection.execute(text(f'ALTER TABLE {table} ADD CONSTRAINT {q(f"uq_{name}_uuid")} UNIQUE (uuid)'))
    # Grants on these tables are table-level, so the restricted runtime role can
    # already read and write the new column; nothing to grant here.


def upgrade():
    connection = op.get_bind()
    reconcile(connection, connection.execute(text('SELECT current_schema()')).scalar_one())


def downgrade():
    raise RuntimeError('Preserve record identifiers devices may hold; use a reviewed migration')
