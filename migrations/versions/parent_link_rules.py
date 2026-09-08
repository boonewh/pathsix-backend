"""Enforce the existing API parent-count rules at the database boundary."""
from alembic import op
from sqlalchemy import text

revision = 'parent_link_rules'
down_revision = 'tenant_row_security'
branch_labels = None
depends_on = None

RULES = (
    ('contacts', 'ck_contacts_one_parent', 'num_nonnulls(client_id, lead_id) = 1'),
    ('interactions', 'ck_interactions_one_parent', 'num_nonnulls(client_id, lead_id, project_id) = 1'),
    ('projects', 'ck_projects_at_most_one_parent', 'num_nonnulls(client_id, lead_id) <= 1'),
)


def reconcile(connection, schema):
    if connection.dialect.name != 'postgresql':
        raise RuntimeError('Parent-link rules require PostgreSQL')
    q = connection.dialect.identifier_preparer.quote
    connection.execute(text("SET LOCAL lock_timeout='5s'"))
    connection.execute(text("SET LOCAL statement_timeout='60s'"))
    for table, _, rule in RULES:
        invalid = connection.execute(text(f'SELECT count(*) FROM {q(schema)}.{q(table)} WHERE NOT ({rule})')).scalar_one()
        if invalid:
            raise RuntimeError(f'{table}: {invalid} invalid parent combinations; no data repaired')
    for table, name, rule in RULES:
        qualified = f'{q(schema)}.{q(table)}'
        # A conflicting name fails the transaction instead of masking schema drift.
        connection.execute(text(f'ALTER TABLE {qualified} ADD CONSTRAINT {q(name)} CHECK ({rule}) NOT VALID'))
        connection.execute(text(f'ALTER TABLE {qualified} VALIDATE CONSTRAINT {q(name)}'))


def upgrade():
    connection = op.get_bind()
    reconcile(connection, connection.execute(text('SELECT current_schema()')).scalar_one())


def downgrade():
    raise RuntimeError('Removing parent-link protections requires a reviewed migration')
