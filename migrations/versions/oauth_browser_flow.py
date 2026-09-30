"""Pre-registered public clients and owner-scoped opaque credentials."""
from alembic import op
from sqlalchemy import text, inspect

revision = 'oauth_browser_flow'
down_revision = 'ai_consent_grants'
branch_labels = depends_on = None


def secure(connection, schema, runtime_role):
    if connection.dialect.name != 'postgresql' or not runtime_role:
        raise RuntimeError('PostgreSQL and restricted runtime role required')
    q = connection.dialect.identifier_preparer.quote
    table = lambda name: f'{q(schema)}.{q(name)}'
    owner = connection.execute(text('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')).scalar_one()
    runtime = connection.execute(text('SELECT rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb FROM pg_roles WHERE rolname=:role'), {'role':runtime_role}).scalar_one()
    if not owner or runtime:
        raise RuntimeError('Expected operator owner and restricted runtime role')
    if connection.execute(text("SELECT 1 FROM pg_policies WHERE schemaname=:schema AND tablename='oauth_credentials'"), {'schema':schema}).first():
        raise RuntimeError('Existing credential policy requires review')
    connection.execute(text(f'REVOKE ALL ON {table("oauth_credentials")} FROM PUBLIC, {q(runtime_role)}'))
    connection.execute(text(f'GRANT SELECT, INSERT, UPDATE ON {table("oauth_credentials")} TO {q(runtime_role)}'))
    own = "tenant_id=NULLIF(current_setting('crm.tenant_id',true),'')::integer AND user_id=NULLIF(current_setting('crm.user_id',true),'')::integer"
    connection.execute(text(f'CREATE POLICY crm_all ON {table("oauth_credentials")} FOR ALL TO PUBLIC USING ({own}) WITH CHECK ({own})'))
    connection.execute(text(f'ALTER TABLE {table("oauth_credentials")} ENABLE ROW LEVEL SECURITY'))
    connection.execute(text(f'ALTER TABLE {table("oauth_credentials")} FORCE ROW LEVEL SECURITY'))
    connection.execute(text(f'''CREATE FUNCTION {table('crm_oauth_immutable')}()
        RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $fn$
        BEGIN
          IF ROW(NEW.token_hash,NEW.tenant_id,NEW.user_id,NEW.connection_id,NEW.kind,NEW.scopes::jsonb,
                 NEW.created_at,NEW.expires_at,NEW.redirect_uri,NEW.code_challenge,NEW.intent_hash)
             IS DISTINCT FROM
             ROW(OLD.token_hash,OLD.tenant_id,OLD.user_id,OLD.connection_id,OLD.kind,OLD.scopes::jsonb,
                 OLD.created_at,OLD.expires_at,OLD.redirect_uri,OLD.code_challenge,OLD.intent_hash)
             OR (OLD.used_at IS NOT NULL AND NEW.used_at IS DISTINCT FROM OLD.used_at)
          THEN RAISE EXCEPTION 'Credential identity and lifetime are immutable' USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END $fn$'''))
    connection.execute(text(f'REVOKE ALL ON FUNCTION {table("crm_oauth_immutable")}() FROM PUBLIC'))
    connection.execute(text(f'CREATE TRIGGER crm_oauth_immutable BEFORE UPDATE ON {table("oauth_credentials")} FOR EACH ROW EXECUTE FUNCTION {table("crm_oauth_immutable")}()'))
    # Only the IDs associated with possession of a high-entropy secret are exposed.
    # No credential rows, grant details or tenant business data cross bootstrap.
    connection.execute(text(f'''CREATE FUNCTION {table('crm_oauth_identity')}(p_hash text)
        RETURNS TABLE(user_id integer, tenant_id integer) LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path=pg_catalog AS $fn$
        SELECT c.user_id,c.tenant_id FROM {table('oauth_credentials')} c WHERE c.token_hash=p_hash
        $fn$'''))
    connection.execute(text(f'REVOKE ALL ON FUNCTION {table("crm_oauth_identity")}(text) FROM PUBLIC'))
    connection.execute(text(f'GRANT EXECUTE ON FUNCTION {table("crm_oauth_identity")}(text) TO {q(runtime_role)}'))


def reconcile(connection, schema, role):
    if connection.dialect.name != 'postgresql' or not role:
        raise RuntimeError('PostgreSQL and explicit runtime role required')
    connection.execute(text("SET LOCAL lock_timeout='5s'"))
    connection.execute(text("SET LOCAL statement_timeout='60s'"))
    if 'oauth_credentials' in inspect(connection).get_table_names(schema=schema):
        raise RuntimeError('Existing credential table requires review')
    q = connection.dialect.identifier_preparer.quote
    table = lambda name: f'{q(schema)}.{q(name)}'
    connection.execute(text(f"ALTER TABLE {table('ai_clients')} ADD COLUMN redirect_uris json NOT NULL DEFAULT '[]', ADD COLUMN oauth_enabled boolean NOT NULL DEFAULT false"))
    connection.execute(text(f'ALTER TABLE {table("ai_connections")} ADD CONSTRAINT uq_ai_connections_owner_id UNIQUE(tenant_id,user_id,id)'))
    connection.execute(text(f'''CREATE TABLE {table('oauth_credentials')} (
        token_hash varchar(64) PRIMARY KEY,
        tenant_id integer NOT NULL REFERENCES {table('tenants')}(id), user_id integer NOT NULL,
        connection_id varchar(36) NOT NULL, kind varchar(10) NOT NULL, scopes json NOT NULL,
        created_at timestamp NOT NULL, expires_at timestamp NOT NULL, used_at timestamp,
        redirect_uri varchar(1000), code_challenge varchar(43), intent_hash varchar(64) UNIQUE,
        CONSTRAINT fk_oauth_credentials_owner_grant FOREIGN KEY(tenant_id,user_id,connection_id)
          REFERENCES {table('ai_connections')}(tenant_id,user_id,id),
        CONSTRAINT ck_oauth_credential_kind CHECK(kind IN ('code','access','refresh')))'''))
    connection.execute(text(f'CREATE INDEX ix_oauth_credentials_owner_grant ON {table("oauth_credentials")} (tenant_id,user_id,connection_id,created_at)'))
    secure(connection,schema,role)


def upgrade():
    connection = op.get_bind()
    reconcile(connection,connection.execute(text('SELECT current_schema()')).scalar_one(),
              op.get_context().config.attributes.get('runtime_role'))


def downgrade():
    raise RuntimeError('Preserve credential and revocation history; use a reviewed migration')
