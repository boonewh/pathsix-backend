"""Activate prepared RLS after the application supports transaction-local identity."""
from alembic import op
from sqlalchemy import text
from migrations.versions.tenant_rls_prepare import TABLES, BUSINESS

revision = 'tenant_row_security'
down_revision = 'tenant_rls_prepare'
branch_labels = None
depends_on = None


def reconcile(connection, schema):
    expected = {(t, 'crm_all', 'ALL') for t in BUSINESS + ('user_preferences',)}
    expected.add(('tenants', 'crm_select', 'SELECT'))
    for table in ('users', 'user_roles'):
        expected.update((table, 'crm_' + cmd.lower(), cmd) for cmd in ('SELECT', 'INSERT', 'UPDATE', 'DELETE'))
    actual = set(connection.execute(text(
        'SELECT tablename, policyname, cmd FROM pg_policies WHERE schemaname=:schema'
    ), {'schema': schema}).all())
    if actual != expected:
        raise RuntimeError('Prepared policy inventory mismatch')
    q = connection.dialect.identifier_preparer.quote
    connection.execute(text("SET LOCAL lock_timeout='5s'"))
    connection.execute(text("SET LOCAL statement_timeout='60s'"))
    for table in TABLES:
        qualified = f'{q(schema)}.{q(table)}'
        connection.execute(text(f'ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY'))
        connection.execute(text(f'ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY'))


def upgrade():
    connection = op.get_bind()
    reconcile(connection, connection.execute(text('SELECT current_schema()')).scalar_one())


def downgrade():
    raise RuntimeError('Disabling tenant row security requires an explicit reviewed operation')
