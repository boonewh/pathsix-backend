"""Reconcile tenant membership and index drift, merging the three legacy heads.

This deliberately does not replay historical migrations or claim tenant RLS.
"""
from alembic import op
from sqlalchemy import inspect, text

revision = 'tenant_membership_indexes'
down_revision = ('add_project_assigned_to', 'add_subscriptions_table', 'add_tenants_table')
branch_labels = None
depends_on = None

TABLES = ('accounts', 'activity_logs', 'chat_messages', 'clients', 'contacts',
          'files', 'interactions', 'leads', 'projects', 'subscriptions', 'users')


def reconcile(connection, schema):
    """Apply in the caller's transaction; any failure must roll back the whole step."""
    if connection.dialect.name != 'postgresql':
        raise RuntimeError('Tenant membership migration requires PostgreSQL')
    quote = connection.dialect.identifier_preparer.quote
    qualified = lambda name: f'{quote(schema)}.{quote(name)}'
    connection.execute(text("SET LOCAL lock_timeout = '5s'"))
    connection.execute(text("SET LOCAL statement_timeout = '60s'"))
    inspector = inspect(connection)
    present = set(inspector.get_table_names(schema=schema))
    if not set(TABLES + ('tenants',)).issubset(present):
        raise RuntimeError('Expected CRM tables are missing; refusing partial migration')

    # Complete the data preflight before creating any objects. Do not repair rows.
    for table in TABLES:
        columns = {c['name']: c for c in inspector.get_columns(table, schema=schema)}
        if 'tenant_id' not in columns or columns['tenant_id']['nullable']:
            raise RuntimeError(f'{table}: expected non-null tenant_id')
        invalid = connection.execute(text(
            f'SELECT count(*) FROM {qualified(table)} c LEFT JOIN {qualified("tenants")} t '
            'ON t.id=c.tenant_id WHERE t.id IS NULL'
        )).scalar_one()
        if invalid:
            raise RuntimeError(f'{table}: {invalid} invalid tenant references; no changes applied')

    for table in TABLES:
        indexes = inspector.get_indexes(table, schema=schema)
        valid_indexes = set(connection.execute(text(
            'SELECT i.relname FROM pg_index x JOIN pg_class i ON i.oid=x.indexrelid '
            'JOIN pg_namespace n ON n.oid=i.relnamespace '
            'WHERE n.nspname=:schema AND x.indisvalid AND x.indisready'
        ), {'schema': schema}).scalars())
        # A partial index does not cover every tenant-owned row.
        suitable = any(idx['column_names'][:1] == ['tenant_id']
                       and idx['name'] in valid_indexes
                       and not idx.get('dialect_options', {}).get('postgresql_where')
                       for idx in indexes)
        if not suitable:
            # No IF NOT EXISTS: a conflicting name must fail rather than mask drift.
            connection.execute(text(f'CREATE INDEX {quote("ix_" + table + "_tenant_membership")} '
                                    f'ON {qualified(table)} (tenant_id)'))
        foreign_keys = inspector.get_foreign_keys(table, schema=schema)
        existing = [fk for fk in foreign_keys
                    if fk['constrained_columns'] == ['tenant_id']
                    and fk['referred_table'] == 'tenants'
                    and fk['referred_columns'] == ['id']
                    and fk['referred_schema'] in (None, schema)]
        names = [fk['name'] for fk in existing]
        if not names:
            name = 'fk_' + table + '_tenant_membership'
            connection.execute(text(f'ALTER TABLE {qualified(table)} ADD CONSTRAINT {quote(name)} '
                                    f'FOREIGN KEY (tenant_id) REFERENCES {qualified("tenants")} (id) NOT VALID'))
            names = [name]
        for name in names:
            connection.execute(text(f'ALTER TABLE {qualified(table)} VALIDATE CONSTRAINT {quote(name)}'))


def upgrade():
    connection = op.get_bind()
    schema = connection.execute(text('SELECT current_schema()')).scalar_one()
    reconcile(connection, schema)


def downgrade():
    # Existing matching objects may predate this migration. Never guess which to drop.
    raise RuntimeError('Tenant protection downgrade requires an explicit reviewed migration')
