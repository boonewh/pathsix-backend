"""Self-registered public AI clients (CIMD and DCR) beside operator clients."""
from alembic import op
from sqlalchemy import text, inspect

revision = 'mcp_client_registration'
down_revision = 'mcp_lead_creation'
branch_labels = depends_on = None

# Keep in step with app.services.ai_connections.PUBLISHED_SCOPES.
PUBLISHED = ('clients:read', 'leads:read', 'leads:create')
MAX_DCR_PER_HOUR = 200


def secure(connection, schema, runtime_role):
    if connection.dialect.name != 'postgresql' or not runtime_role:
        raise RuntimeError('PostgreSQL and explicit runtime role required')
    q = connection.dialect.identifier_preparer.quote
    table = f'{q(schema)}.{q("ai_clients")}'
    connections = f'{q(schema)}.{q("ai_connections")}'
    operator = connection.execute(text('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')).scalar_one()
    unsafe = connection.execute(text('SELECT rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb FROM pg_roles WHERE rolname=:role'), {'role':runtime_role}).scalar_one()
    if not operator or unsafe:
        raise RuntimeError('Expected operator and restricted runtime role')
    if connection.execute(text(f"SELECT has_table_privilege(:role,'{schema}.ai_clients','INSERT') OR has_table_privilege(:role,'{schema}.ai_clients','UPDATE')"), {'role':runtime_role}).scalar_one():
        raise RuntimeError('Runtime client catalog write access requires review')
    published = ','.join(f"'{s}'" for s in PUBLISHED)
    function = f'{q(schema)}.crm_register_ai_client'
    # The runtime keeps read-only catalog access. This definer function can only
    # create or refresh self-registered public clients limited to published
    # scopes; operator rows, trust flags and other clients' types never change.
    connection.execute(text(f'''CREATE FUNCTION {function}(p_id text, p_name text, p_redirects json,
            p_registration text, p_scopes json)
        RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
        DECLARE existing text;
        BEGIN
          IF p_registration NOT IN ('cimd','dcr') OR p_id IS NULL OR length(p_id) NOT BETWEEN 1 AND 200
             OR p_name IS NULL OR length(p_name) NOT BETWEEN 1 AND 100
             OR p_redirects IS NULL OR p_scopes IS NULL
             OR json_typeof(p_redirects) <> 'array' OR json_array_length(p_redirects) NOT BETWEEN 1 AND 10
             OR json_typeof(p_scopes) <> 'array' OR json_array_length(p_scopes) < 1
             OR EXISTS (SELECT 1 FROM json_array_elements(p_redirects) r
                        WHERE json_typeof(r) <> 'string' OR length(r #>> '{{}}') > 1000)
             OR EXISTS (SELECT 1 FROM json_array_elements(p_scopes) s
                        WHERE json_typeof(s) <> 'string' OR (s #>> '{{}}') NOT IN ({published}))
          THEN RAISE EXCEPTION 'Invalid client registration' USING ERRCODE='22023';
          END IF;
          SELECT registration INTO existing FROM {table} WHERE id = p_id FOR UPDATE;
          IF existing IS NOT NULL AND existing <> p_registration THEN
            RAISE EXCEPTION 'Client identifier unavailable' USING ERRCODE='23505';
          END IF;
          IF p_registration = 'dcr' THEN
            DELETE FROM {table} c WHERE c.registration = 'dcr'
              AND c.created_at < (now() AT TIME ZONE 'UTC') - interval '1 day'
              AND NOT EXISTS (SELECT 1 FROM {connections} a WHERE a.client_id = c.id);
            IF (SELECT count(*) FROM {table} WHERE registration = 'dcr'
                AND created_at > (now() AT TIME ZONE 'UTC') - interval '1 hour') >= {MAX_DCR_PER_HOUR} THEN
              RAISE EXCEPTION 'Client registration limit reached' USING ERRCODE='54000';
            END IF;
          END IF;
          IF existing IS NULL THEN
            INSERT INTO {table} (id, name, is_active, allowed_scopes, redirect_uris, oauth_enabled,
                                 registration, created_at, metadata_fetched_at, inline_review)
            VALUES (p_id, p_name, true, p_scopes, p_redirects, true, p_registration,
                    now() AT TIME ZONE 'UTC', now() AT TIME ZONE 'UTC', false);
          ELSE
            UPDATE {table} SET name = p_name, redirect_uris = p_redirects, allowed_scopes = p_scopes,
                   metadata_fetched_at = now() AT TIME ZONE 'UTC'
             WHERE id = p_id;
          END IF;
        END $fn$'''))
    connection.execute(text(f'REVOKE ALL ON FUNCTION {function}(text,text,json,text,json) FROM PUBLIC'))
    connection.execute(text(f'GRANT EXECUTE ON FUNCTION {function}(text,text,json,text,json) TO {q(runtime_role)}'))


def reconcile(connection, schema, role):
    if connection.dialect.name != 'postgresql' or not role:
        raise RuntimeError('PostgreSQL and explicit runtime role required')
    connection.execute(text("SET LOCAL lock_timeout='5s'"))
    connection.execute(text("SET LOCAL statement_timeout='60s'"))
    if 'registration' in {c['name'] for c in inspect(connection).get_columns('ai_clients', schema=schema)}:
        raise RuntimeError('Existing client registration columns require review')
    q = connection.dialect.identifier_preparer.quote
    table = f'{q(schema)}.{q("ai_clients")}'
    connection.execute(text(f'''ALTER TABLE {table}
        ADD COLUMN registration varchar(10) NOT NULL DEFAULT 'operator',
        ADD COLUMN created_at timestamp NOT NULL DEFAULT (now() AT TIME ZONE 'UTC'),
        ADD COLUMN metadata_fetched_at timestamp,
        ADD COLUMN inline_review boolean NOT NULL DEFAULT false,
        ADD CONSTRAINT ck_ai_clients_registration CHECK (registration IN ('operator','cimd','dcr')),
        ADD CONSTRAINT ck_ai_clients_inline_operator CHECK (NOT inline_review OR registration = 'operator')'''))
    # Existing operator OAuth clients keep the inline review they already use.
    connection.execute(text(f'UPDATE {table} SET inline_review = true WHERE oauth_enabled'))
    connection.execute(text(f'CREATE INDEX ix_ai_clients_registration_created ON {table} (registration, created_at)'))
    secure(connection, schema, role)


def upgrade():
    connection = op.get_bind()
    reconcile(connection, connection.execute(text('SELECT current_schema()')).scalar_one(),
              op.get_context().config.attributes.get('runtime_role'))


def downgrade():
    raise RuntimeError('Preserve client registrations referenced by grants; use a reviewed migration')
