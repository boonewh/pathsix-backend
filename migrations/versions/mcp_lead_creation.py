"""Owner-scoped immutable proposals and durable terminal write receipts."""
from alembic import op
from sqlalchemy import text, inspect

revision = 'mcp_lead_creation'
down_revision = 'mcp_read_audit'
branch_labels = depends_on = None


def secure(connection, schema, runtime_role):
    if connection.dialect.name != 'postgresql' or not runtime_role:
        raise RuntimeError('PostgreSQL and explicit runtime role required')
    q = connection.dialect.identifier_preparer.quote
    table = f'{q(schema)}.ai_write_actions'
    operator = connection.execute(text('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')).scalar_one()
    unsafe = connection.execute(text('SELECT rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb FROM pg_roles WHERE rolname=:role'), {'role':runtime_role}).scalar_one()
    if not operator or unsafe:
        raise RuntimeError('Expected operator and restricted runtime role')
    if connection.execute(text("SELECT 1 FROM pg_policies WHERE schemaname=:schema AND tablename='ai_write_actions'"), {'schema':schema}).first():
        raise RuntimeError('Existing action policies require review')
    connection.execute(text(f'REVOKE ALL ON {table} FROM PUBLIC, {q(runtime_role)}'))
    connection.execute(text(f'GRANT SELECT, INSERT ON {table} TO {q(runtime_role)}'))
    connection.execute(text(f'GRANT UPDATE (status, result_id, decided_at) ON {table} TO {q(runtime_role)}'))
    own = "tenant_id=NULLIF(current_setting('crm.tenant_id',true),'')::integer AND user_id=NULLIF(current_setting('crm.user_id',true),'')::integer"
    connection.execute(text(f'CREATE POLICY crm_all ON {table} FOR ALL TO PUBLIC USING ({own}) WITH CHECK ({own})'))
    connection.execute(text(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY'))
    connection.execute(text(f'ALTER TABLE {table} FORCE ROW LEVEL SECURITY'))
    function = f'{q(schema)}.crm_action_immutable'
    connection.execute(text(f'''CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql
        SET search_path=pg_catalog AS $fn$
        BEGIN
          IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'pending' OR NEW.result_id IS NOT NULL OR NEW.decided_at IS NOT NULL THEN
              RAISE EXCEPTION 'Actions must begin pending' USING ERRCODE='23514';
            END IF;
          ELSE
            IF ROW(NEW.id,NEW.tenant_id,NEW.user_id,NEW.connection_id,NEW.request_key,
                   NEW.input_hash,NEW.kind,NEW.payload::jsonb,NEW.created_at,NEW.expires_at)
               IS DISTINCT FROM
               ROW(OLD.id,OLD.tenant_id,OLD.user_id,OLD.connection_id,OLD.request_key,
                   OLD.input_hash,OLD.kind,OLD.payload::jsonb,OLD.created_at,OLD.expires_at)
               OR OLD.status <> 'pending' OR NEW.status NOT IN ('cancelled','committed')
            THEN RAISE EXCEPTION 'Proposal and terminal outcome are immutable' USING ERRCODE='23514';
            END IF;
          END IF;
          RETURN NEW;
        END $fn$'''))
    connection.execute(text(f'REVOKE ALL ON FUNCTION {function}() FROM PUBLIC'))
    connection.execute(text(f'CREATE TRIGGER crm_action_immutable BEFORE INSERT OR UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION {function}()'))


def reconcile(connection, schema, role):
    if connection.dialect.name != 'postgresql' or not role:
        raise RuntimeError('PostgreSQL and explicit runtime role required')
    connection.execute(text("SET LOCAL lock_timeout='5s'"))
    connection.execute(text("SET LOCAL statement_timeout='60s'"))
    if 'ai_write_actions' in inspect(connection).get_table_names(schema=schema):
        raise RuntimeError('Existing action table requires review')
    q = connection.dialect.identifier_preparer.quote
    table = lambda name: f'{q(schema)}.{q(name)}'
    connection.execute(text(f'''CREATE TABLE {table('ai_write_actions')} (
        id varchar(36) PRIMARY KEY, tenant_id integer NOT NULL REFERENCES {table('tenants')}(id),
        user_id integer NOT NULL, connection_id varchar(36) NOT NULL,
        request_key varchar(64) NOT NULL, input_hash varchar(64) NOT NULL,
        kind varchar(30) NOT NULL, payload json NOT NULL, status varchar(12) NOT NULL,
        result_id integer, created_at timestamp NOT NULL, expires_at timestamp NOT NULL, decided_at timestamp,
        CONSTRAINT fk_ai_write_actions_owner_grant FOREIGN KEY(tenant_id,user_id,connection_id)
          REFERENCES {table('ai_connections')}(tenant_id,user_id,id),
        CONSTRAINT uq_ai_write_actions_request UNIQUE(connection_id,request_key),
        CONSTRAINT ck_ai_write_actions_kind CHECK(kind='create_lead'),
        CONSTRAINT ck_ai_write_actions_status CHECK(status IN ('pending','cancelled','committed')),
        CONSTRAINT ck_ai_write_actions_expiry CHECK(expires_at > created_at),
        CONSTRAINT ck_ai_write_actions_result CHECK(
          (status='pending' AND result_id IS NULL AND decided_at IS NULL) OR
          (status='cancelled' AND result_id IS NULL AND decided_at IS NOT NULL) OR
          (status='committed' AND result_id IS NOT NULL AND decided_at IS NOT NULL)))'''))
    connection.execute(text(f'CREATE INDEX ix_ai_write_actions_owner_created ON {table("ai_write_actions")} (tenant_id,user_id,created_at)'))
    secure(connection, schema, role)


def upgrade():
    connection = op.get_bind()
    reconcile(connection, connection.execute(text('SELECT current_schema()')).scalar_one(),
              op.get_context().config.attributes.get('runtime_role'))


def downgrade():
    raise RuntimeError('Preserve action and receipt history; use a reviewed migration')
