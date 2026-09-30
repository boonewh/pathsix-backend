"""Owner-bound append-only metadata for read-only MCP invocations."""
from alembic import op
from sqlalchemy import text, inspect

revision = 'mcp_read_audit'
down_revision = 'oauth_browser_flow'
branch_labels = depends_on = None


def secure(connection,schema,runtime_role):
    if connection.dialect.name!='postgresql' or not runtime_role:
        raise RuntimeError('PostgreSQL and explicit runtime role required')
    q=connection.dialect.identifier_preparer.quote
    table=f'{q(schema)}.ai_tool_audits'
    owner=connection.execute(text('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')).scalar_one()
    unsafe=connection.execute(text('SELECT rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb FROM pg_roles WHERE rolname=:role'),{'role':runtime_role}).scalar_one()
    if not owner or unsafe: raise RuntimeError('Expected operator and restricted runtime role')
    if connection.execute(text("SELECT 1 FROM pg_policies WHERE schemaname=:schema AND tablename='ai_tool_audits'"),{'schema':schema}).first():
        raise RuntimeError('Existing audit policies require review')
    connection.execute(text(f'REVOKE ALL ON {table} FROM PUBLIC, {q(runtime_role)}'))
    connection.execute(text(f'GRANT SELECT, INSERT ON {table} TO {q(runtime_role)}'))
    own="tenant_id=NULLIF(current_setting('crm.tenant_id',true),'')::integer AND user_id=NULLIF(current_setting('crm.user_id',true),'')::integer"
    connection.execute(text(f'CREATE POLICY crm_select ON {table} FOR SELECT TO PUBLIC USING ({own})'))
    connection.execute(text(f'CREATE POLICY crm_insert ON {table} FOR INSERT TO PUBLIC WITH CHECK ({own})'))
    connection.execute(text(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY'))
    connection.execute(text(f'ALTER TABLE {table} FORCE ROW LEVEL SECURITY'))


def reconcile(connection,schema,role):
    if connection.dialect.name!='postgresql' or not role: raise RuntimeError('PostgreSQL and runtime role required')
    connection.execute(text("SET LOCAL lock_timeout='5s'"))
    connection.execute(text("SET LOCAL statement_timeout='60s'"))
    if 'ai_tool_audits' in inspect(connection).get_table_names(schema=schema): raise RuntimeError('Existing audit table requires review')
    q=connection.dialect.identifier_preparer.quote
    table=lambda name:f'{q(schema)}.{q(name)}'
    connection.execute(text(f'''CREATE TABLE {table('ai_tool_audits')} (
        id varchar(36) PRIMARY KEY, tenant_id integer NOT NULL REFERENCES {table('tenants')}(id),
        user_id integer NOT NULL, connection_id varchar(36) NOT NULL, tool varchar(40) NOT NULL,
        outcome varchar(30) NOT NULL, result_count integer NOT NULL, created_at timestamp NOT NULL,
        CONSTRAINT fk_ai_tool_audits_owner_grant FOREIGN KEY(tenant_id,user_id,connection_id)
          REFERENCES {table('ai_connections')}(tenant_id,user_id,id),
        CONSTRAINT ck_ai_tool_audits_outcome CHECK(outcome IN ('success','not_found','invalid_arguments','forbidden','unknown_tool')))'''))
    connection.execute(text(f'CREATE INDEX ix_ai_tool_audits_owner_created ON {table("ai_tool_audits")} (tenant_id,user_id,connection_id,created_at)'))
    secure(connection,schema,role)


def upgrade():
    connection=op.get_bind()
    reconcile(connection,connection.execute(text('SELECT current_schema()')).scalar_one(),op.get_context().config.attributes.get('runtime_role'))


def downgrade():
    raise RuntimeError('Preserve invocation history; use a reviewed migration')
