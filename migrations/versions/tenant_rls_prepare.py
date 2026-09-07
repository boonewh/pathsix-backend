"""Deny unscoped and cross-tenant database access, including raw SQL."""
from alembic import op
from sqlalchemy import text

revision = 'tenant_rls_prepare'
down_revision = 'tenant_relationships'
branch_labels = None
depends_on = None

BUSINESS = ('accounts', 'activity_logs', 'chat_messages', 'clients', 'contacts',
            'files', 'interactions', 'leads', 'projects', 'subscriptions')
TABLES = BUSINESS + ('users', 'tenants', 'user_roles', 'user_preferences')


def reconcile(connection, schema, runtime_role):
    if connection.dialect.name != 'postgresql' or not runtime_role:
        raise RuntimeError('PostgreSQL and an explicit runtime role are required')
    q = connection.dialect.identifier_preparer.quote
    table = lambda name: f'{q(schema)}.{q(name)}'
    connection.execute(text("SET LOCAL lock_timeout='5s'"))
    connection.execute(text("SET LOCAL statement_timeout='60s'"))
    owner = connection.execute(text('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')).scalar_one()
    unsafe = connection.execute(text('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=:role'), {'role': runtime_role}).scalar_one()
    if not owner or unsafe:
        raise RuntimeError('Expected an operator owner and a restricted runtime role')
    policies = connection.execute(text('SELECT tablename FROM pg_policies WHERE schemaname=:schema'), {'schema': schema}).scalars()
    if set(policies).intersection(TABLES):
        raise RuntimeError('Existing policies require review; refusing additive policy bypass')

    # Narrow bootstrap: return only identity IDs, never credentials or CRM rows.
    # Qualified objects and a fixed search_path prevent SECURITY DEFINER hijacking.
    function = table('crm_auth_identity')
    connection.execute(text(f'''CREATE FUNCTION {function}(p_user_id integer, p_email text)
        RETURNS TABLE(user_id integer, tenant_id integer)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog
        AS $fn$ SELECT u.id, u.tenant_id FROM {table('users')} u
          WHERE (p_user_id IS NOT NULL AND p_email IS NULL AND u.id=p_user_id)
             OR (p_user_id IS NULL AND p_email IS NOT NULL AND u.email=p_email) $fn$'''))
    connection.execute(text(f'REVOKE ALL ON FUNCTION {function}(integer, text) FROM PUBLIC'))
    connection.execute(text(f'GRANT EXECUTE ON FUNCTION {function}(integer, text) TO {q(runtime_role)}'))
    setting = lambda key: f"NULLIF(current_setting('crm.{key}', true), '')::integer"
    tenant, auth_user, auth_tenant, reset_user = [setting(k) for k in ('tenant_id', 'auth_user_id', 'auth_tenant_id', 'reset_user_id')]

    def policy(name, command, using=None, check=None):
        sql = f'CREATE POLICY {q("crm_" + command.lower())} ON {table(name)} FOR {command} TO PUBLIC'
        if using is not None:
            sql += f' USING ({using})'
        if check is not None:
            sql += f' WITH CHECK ({check})'
        connection.execute(text(sql))

    for name in BUSINESS:
        policy(name, 'ALL', f'tenant_id={tenant}', f'tenant_id={tenant}')
    policy('users', 'SELECT', f'tenant_id={tenant} OR (id={auth_user} AND tenant_id={auth_tenant})')
    policy('users', 'UPDATE', f'tenant_id={tenant} OR id={reset_user}', f'tenant_id={tenant} OR (id={reset_user} AND tenant_id={auth_tenant})')
    policy('users', 'INSERT', check=f'tenant_id={tenant}')
    policy('users', 'DELETE', f'tenant_id={tenant}')
    policy('tenants', 'SELECT', f'id={tenant} OR id={auth_tenant}')
    for name in ('user_preferences', 'user_roles'):
        own = f'EXISTS (SELECT 1 FROM {table("users")} u WHERE u.id={table(name)}.user_id AND u.tenant_id={tenant})'
        if name == 'user_roles':
            policy(name, 'SELECT', f'{own} OR user_id={auth_user}')
            policy(name, 'INSERT', check=own)
            policy(name, 'UPDATE', own, own)
            policy(name, 'DELETE', own)
        else:
            policy(name, 'ALL', own, own)


def upgrade():
    connection = op.get_bind()
    runtime_role = op.get_context().config.attributes.get('runtime_role')
    reconcile(connection, connection.execute(text('SELECT current_schema()')).scalar_one(), runtime_role)


def downgrade():
    raise RuntimeError('Disabling tenant row security requires an explicit reviewed operation')
