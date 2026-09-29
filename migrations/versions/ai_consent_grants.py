"""Owner-scoped AI consent records; no token storage or public client enrollment."""
from alembic import op
from sqlalchemy import text, inspect

revision = 'ai_consent_grants'
down_revision = 'parent_link_rules'
branch_labels = None
depends_on = None


def secure(connection, schema, runtime_role):
    """Apply policy/grants to newly created tables in the caller's transaction."""
    if connection.dialect.name != 'postgresql' or not runtime_role:
        raise RuntimeError('PostgreSQL and an explicit runtime role are required')
    q = connection.dialect.identifier_preparer.quote
    table = lambda name: f'{q(schema)}.{q(name)}'
    owner = connection.execute(text('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')).scalar_one()
    runtime = connection.execute(text('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=:role'), {'role': runtime_role}).scalar_one()
    if not owner or runtime:
        raise RuntimeError('Expected operator owner and restricted runtime role')
    if connection.execute(text('SELECT 1 FROM pg_policies WHERE schemaname=:schema AND tablename=:table'),
                          {'schema': schema, 'table': 'ai_connections'}).first():
        raise RuntimeError('Existing AI connection policy requires review')
    connection.execute(text(f'REVOKE ALL ON {table("ai_clients")}, {table("ai_connections")} FROM PUBLIC'))
    connection.execute(text(f'REVOKE ALL ON {table("ai_clients")}, {table("ai_connections")} FROM {q(runtime_role)}'))
    connection.execute(text(f'GRANT SELECT ON {table("ai_clients")} TO {q(runtime_role)}'))
    connection.execute(text(f'GRANT SELECT, INSERT, UPDATE ON {table("ai_connections")} TO {q(runtime_role)}'))
    own = "tenant_id=NULLIF(current_setting('crm.tenant_id', true), '')::integer AND user_id=NULLIF(current_setting('crm.user_id', true), '')::integer"
    connection.execute(text(f'CREATE POLICY crm_all ON {table("ai_connections")} FOR ALL TO PUBLIC USING ({own}) WITH CHECK ({own})'))
    connection.execute(text(f'ALTER TABLE {table("ai_connections")} ENABLE ROW LEVEL SECURITY'))
    connection.execute(text(f'ALTER TABLE {table("ai_connections")} FORCE ROW LEVEL SECURITY'))
    connection.execute(text(f'''CREATE FUNCTION {table('crm_ai_connection_immutable')}()
        RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $fn$
        BEGIN
          IF ROW(NEW.id,NEW.tenant_id,NEW.user_id,NEW.client_id,NEW.resource,NEW.scopes::jsonb,NEW.created_at,NEW.expires_at)
             IS DISTINCT FROM
             ROW(OLD.id,OLD.tenant_id,OLD.user_id,OLD.client_id,OLD.resource,OLD.scopes::jsonb,OLD.created_at,OLD.expires_at)
             OR (OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at)
          THEN RAISE EXCEPTION 'Consent identity and permissions are immutable; create a new grant'
               USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END $fn$'''))
    connection.execute(text(f'REVOKE ALL ON FUNCTION {table("crm_ai_connection_immutable")}() FROM PUBLIC'))
    connection.execute(text(f'CREATE TRIGGER crm_ai_connection_immutable BEFORE UPDATE ON {table("ai_connections")} '
                            f'FOR EACH ROW EXECUTE FUNCTION {table("crm_ai_connection_immutable")}()'))


def reconcile(connection, schema, role):
    if connection.dialect.name != 'postgresql':
        raise RuntimeError('AI consent migration requires PostgreSQL')
    if not role:
        raise RuntimeError('Explicit runtime role required')
    connection.execute(text("SET LOCAL lock_timeout='5s'"))
    connection.execute(text("SET LOCAL statement_timeout='60s'"))
    present = set(inspect(connection).get_table_names(schema=schema))
    if present.intersection({'ai_clients', 'ai_connections'}):
        raise RuntimeError('Existing AI tables require review')
    # Explicit DDL keeps this revision independent of future ORM model changes.
    q = connection.dialect.identifier_preparer.quote
    table = lambda name: f'{q(schema)}.{q(name)}'
    connection.execute(text(f'''CREATE TABLE {table('ai_clients')} (
        id varchar(200) PRIMARY KEY, name varchar(100) NOT NULL,
        is_active boolean NOT NULL DEFAULT false, allowed_scopes json NOT NULL DEFAULT '[]')'''))
    connection.execute(text(f'''CREATE TABLE {table('ai_connections')} (
        id varchar(36) PRIMARY KEY, tenant_id integer NOT NULL REFERENCES {table('tenants')}(id),
        user_id integer NOT NULL, client_id varchar(200) NOT NULL REFERENCES {table('ai_clients')}(id),
        resource varchar(500) NOT NULL, scopes json NOT NULL, created_at timestamp NOT NULL,
        expires_at timestamp NOT NULL, revoked_at timestamp, last_used_at timestamp,
        CONSTRAINT fk_ai_connections_owner_same_tenant FOREIGN KEY (tenant_id,user_id)
            REFERENCES {table('users')}(tenant_id,id),
        CONSTRAINT ck_ai_connections_expiry CHECK (expires_at > created_at))'''))
    connection.execute(text(f'CREATE INDEX ix_ai_connections_owner_created ON {table("ai_connections")} (tenant_id,user_id,created_at,id)'))
    secure(connection, schema, role)


def upgrade():
    connection = op.get_bind()
    reconcile(connection, connection.execute(text('SELECT current_schema()')).scalar_one(),
              op.get_context().config.attributes.get('runtime_role'))


def downgrade():
    raise RuntimeError('Preserve consent/revocation records; use an explicitly reviewed migration')
