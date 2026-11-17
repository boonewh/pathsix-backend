"""Require declared record and user relationships to stay within one tenant."""
from alembic import op
from sqlalchemy import inspect, text

revision = 'tenant_relationships'
down_revision = 'tenant_membership_indexes'
branch_labels = None
depends_on = None

# Frozen migration inventory: do not derive historical DDL from changing models.
EDGES = (
    ('accounts', 'client_id', 'clients'),
    ('activity_logs', 'user_id', 'users'),
    ('chat_messages', 'sender_id', 'users'),
    ('chat_messages', 'recipient_id', 'users'),
    ('chat_messages', 'client_id', 'clients'),
    ('chat_messages', 'lead_id', 'leads'),
    ('clients', 'created_by', 'users'),
    ('clients', 'updated_by', 'users'),
    ('clients', 'deleted_by', 'users'),
    ('clients', 'assigned_to', 'users'),
    ('clients', 'source_lead_id', 'leads'),
    ('contacts', 'client_id', 'clients'),
    ('contacts', 'lead_id', 'leads'),
    ('files', 'user_id', 'users'),
    ('interactions', 'client_id', 'clients'),
    ('interactions', 'lead_id', 'leads'),
    ('interactions', 'project_id', 'projects'),
    ('leads', 'created_by', 'users'),
    ('leads', 'updated_by', 'users'),
    ('leads', 'deleted_by', 'users'),
    ('leads', 'assigned_to', 'users'),
    ('projects', 'client_id', 'clients'),
    ('projects', 'lead_id', 'leads'),
    ('projects', 'created_by', 'users'),
    ('projects', 'last_updated_by', 'users'),
    ('projects', 'deleted_by', 'users'),
    ('projects', 'assigned_to', 'users'),
    ('subscriptions', 'client_id', 'clients'),
    ('subscriptions', 'created_by', 'users'),
    ('subscriptions', 'updated_by', 'users'),
)


def reconcile(connection, schema):
    if connection.dialect.name != 'postgresql':
        raise RuntimeError('Tenant relationship migration requires PostgreSQL')
    quote = connection.dialect.identifier_preparer.quote
    qualified = lambda name: f'{quote(schema)}.{quote(name)}'
    connection.execute(text("SET LOCAL lock_timeout='5s'"))
    connection.execute(text("SET LOCAL statement_timeout='60s'"))
    inspector = inspect(connection)
    # Refuse orphan/cross-tenant rows before DDL. Never rewrite customer data.
    for child, column, parent in EDGES:
        invalid = connection.execute(text(
            f'SELECT count(*) FROM {qualified(child)} c LEFT JOIN {qualified(parent)} p '
            f'ON p.id=c.{quote(column)} AND p.tenant_id=c.tenant_id '
            f'WHERE c.{quote(column)} IS NOT NULL AND p.id IS NULL'
        )).scalar_one()
        if invalid:
            raise RuntimeError(f'{child}.{column}: {invalid} invalid relationships')

    for parent in sorted({parent for _, _, parent in EDGES}):
        unique_keys = inspector.get_unique_constraints(parent, schema=schema)
        if not any(key['column_names'] == ['tenant_id', 'id'] for key in unique_keys):
            connection.execute(text(f'ALTER TABLE {qualified(parent)} ADD CONSTRAINT '
                                    f'{quote("uq_" + parent + "_tenant_id_id")} UNIQUE (tenant_id, id)'))
    for child, column, parent in EDGES:
        existing = [fk for fk in inspector.get_foreign_keys(child, schema=schema)
                    if fk['constrained_columns'] == ['tenant_id', column]
                    and fk['referred_table'] == parent
                    and fk['referred_schema'] in (None, schema)
                    and fk['referred_columns'] == ['tenant_id', 'id']]
        names = [fk['name'] for fk in existing]
        if not names:
            name = f'fk_{child}_{column}_same_tenant'
            # MATCH SIMPLE intentionally permits optional parent/user IDs to be NULL.
            # Keep original scalar FKs and ORM mappings; these add a DB backstop.
            connection.execute(text(f'ALTER TABLE {qualified(child)} ADD CONSTRAINT {quote(name)} '
                                    f'FOREIGN KEY (tenant_id, {quote(column)}) REFERENCES '
                                    f'{qualified(parent)} (tenant_id, id) MATCH SIMPLE NOT VALID'))
            names = [name]
        for name in names:
            connection.execute(text(f'ALTER TABLE {qualified(child)} VALIDATE CONSTRAINT {quote(name)}'))


def upgrade():
    connection = op.get_bind()
    reconcile(connection, connection.execute(text('SELECT current_schema()')).scalar_one())


def downgrade():
    raise RuntimeError('Removing tenant protections requires an explicit reviewed migration')
