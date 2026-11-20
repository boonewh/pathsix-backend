"""Registered mobile devices and rotating refresh tokens (mobile Phase 0, item 6).

Adds devices (app/services/devices.py). requires_auth reads a device row before
it binds the tenant principal, under the authentication identity, so SELECT is
also allowed for the signed-in user's own rows via crm.auth_user_id and
crm.auth_tenant_id (the same shape as users). Writes need the tenant context.
The runtime role may read, insert and update devices; revocation is an update,
so it never deletes them.

Also adds device_holdings: what the change feed has sent each device, so a
removal reaches only devices that held the record (mobile todo 26). Its rows
are written and acknowledged by the feed and cleared on revocation, so the
runtime role may read, insert and delete them, under tenant row security.
The runtime role is found as in mobile_change_feed.
"""
from alembic import op
from sqlalchemy import inspect, text

from migrations.versions.mobile_change_feed import runtime_role_for, _unsafe

revision = 'mobile_device_auth'
down_revision = 'mobile_idempotent_writes'
branch_labels = depends_on = None

TABLE = 'devices'
HOLDINGS = 'device_holdings'


def _setting(key):
    return f"NULLIF(current_setting('crm.{key}', true), '')::integer"


def secure(connection, schema, runtime_role):
    """Grants and row security for devices (also used by the test fixtures)."""
    if connection.dialect.name != 'postgresql' or not runtime_role:
        raise RuntimeError('PostgreSQL and explicit runtime role required')
    q = connection.dialect.identifier_preparer.quote
    table = f'{q(schema)}.{q(TABLE)}'
    owner = connection.execute(text(
        'SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')).scalar_one()
    if not owner or _unsafe(connection, runtime_role):
        raise RuntimeError('Expected operator and restricted runtime role')
    if connection.execute(text('SELECT 1 FROM pg_policies WHERE schemaname=:schema AND tablename=:table'),
                          {'schema': schema, 'table': TABLE}).first():
        raise RuntimeError('Existing devices policies require review')
    role = q(runtime_role)
    connection.execute(text(f'REVOKE ALL ON {table} FROM PUBLIC, {role}'))
    connection.execute(text(f'GRANT SELECT, INSERT, UPDATE ON {table} TO {role}'))
    own = f"tenant_id = {_setting('tenant_id')}"
    auth = f"user_id = {_setting('auth_user_id')} AND tenant_id = {_setting('auth_tenant_id')}"
    connection.execute(text(f'CREATE POLICY crm_select ON {table} FOR SELECT TO PUBLIC USING ({own} OR ({auth}))'))
    connection.execute(text(f'CREATE POLICY crm_modify ON {table} FOR ALL TO PUBLIC USING ({own}) WITH CHECK ({own})'))
    connection.execute(text(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY'))
    connection.execute(text(f'ALTER TABLE {table} FORCE ROW LEVEL SECURITY'))
    if not connection.execute(text(
            "SELECT has_table_privilege(:role, :table, 'SELECT') AND has_table_privilege(:role, :table, 'INSERT') "
            "AND has_table_privilege(:role, :table, 'UPDATE')"), {'role': runtime_role, 'table': table}).scalar_one():
        raise RuntimeError('Runtime role cannot read, insert and update devices')
    excess = connection.execute(text(
        "SELECT r.rolname FROM pg_roles r WHERE pg_has_role(:role, r.oid, 'MEMBER') AND "
        "has_table_privilege(r.oid, :table, 'DELETE, TRUNCATE, REFERENCES, TRIGGER')"),
        {'role': runtime_role, 'table': table}).scalars().all()
    if excess:
        raise RuntimeError('Runtime role must not delete or truncate devices')
    secure_holdings(connection, schema, runtime_role)


def secure_holdings(connection, schema, runtime_role):
    q = connection.dialect.identifier_preparer.quote
    table = f'{q(schema)}.{q(HOLDINGS)}'
    if connection.execute(text('SELECT 1 FROM pg_policies WHERE schemaname=:schema AND tablename=:table'),
                          {'schema': schema, 'table': HOLDINGS}).first():
        raise RuntimeError('Existing device_holdings policies require review')
    role = q(runtime_role)
    connection.execute(text(f'REVOKE ALL ON {table} FROM PUBLIC, {role}'))
    connection.execute(text(f'GRANT SELECT, INSERT, DELETE ON {table} TO {role}'))
    own = f"tenant_id = {_setting('tenant_id')}"
    connection.execute(text(f'CREATE POLICY crm_all ON {table} FOR ALL TO PUBLIC USING ({own}) WITH CHECK ({own})'))
    connection.execute(text(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY'))
    connection.execute(text(f'ALTER TABLE {table} FORCE ROW LEVEL SECURITY'))
    excess = connection.execute(text(
        "SELECT r.rolname FROM pg_roles r WHERE pg_has_role(:role, r.oid, 'MEMBER') AND ("
        "has_table_privilege(r.oid, :table, 'UPDATE, TRUNCATE, REFERENCES, TRIGGER') "
        "OR has_any_column_privilege(r.oid, :table, 'UPDATE, REFERENCES'))"),
        {'role': runtime_role, 'table': table}).scalars().all()
    if excess:
        raise RuntimeError('Runtime role privileges on device_holdings must be SELECT, INSERT and DELETE only')


def reconcile(connection, schema, runtime_role=None):
    if connection.dialect.name != 'postgresql':
        raise RuntimeError('PostgreSQL required')
    connection.execute(text("SET LOCAL lock_timeout='5s'"))
    connection.execute(text("SET LOCAL statement_timeout='60s'"))
    q = connection.dialect.identifier_preparer.quote
    t = lambda name: f'{q(schema)}.{q(name)}'
    role = runtime_role_for(connection, schema, runtime_role)
    if {TABLE, HOLDINGS} & set(inspect(connection).get_table_names(schema=schema)):
        raise RuntimeError('Existing devices tables require review')
    connection.execute(text(
        f'CREATE TABLE {t(TABLE)} (id uuid PRIMARY KEY, '
        f'tenant_id integer NOT NULL REFERENCES {t("tenants")} (id), user_id integer NOT NULL, '
        'name varchar(100) NOT NULL, platform varchar(20) NOT NULL, created_at timestamp NOT NULL, '
        'last_seen_at timestamp NOT NULL, refresh_hash varchar(64) NOT NULL, previous_refresh_hash varchar(64), '
        'refreshed_at timestamp NOT NULL, refresh_expires_at timestamp NOT NULL, '
        'credential_marker varchar(64) NOT NULL, revoked_at timestamp, revoked_by integer, '
        'sync_generation integer NOT NULL DEFAULT 0, sync_acked_seq bigint NOT NULL DEFAULT 0, '
        'sync_pending_seq bigint, sync_pending_removals json, '
        f'CONSTRAINT fk_devices_user_same_tenant FOREIGN KEY (tenant_id, user_id) '
        f'REFERENCES {t("users")} (tenant_id, id), '
        'CONSTRAINT uq_devices_tenant_id_id UNIQUE (tenant_id, id))'))
    connection.execute(text(f'CREATE INDEX ix_devices_tenant_user ON {t(TABLE)} (tenant_id, user_id)'))
    connection.execute(text(
        f'CREATE TABLE {t(HOLDINGS)} (device_id uuid NOT NULL, entity varchar(20) NOT NULL, '
        f'record_uuid uuid NOT NULL, tenant_id integer NOT NULL REFERENCES {t("tenants")} (id), '
        'PRIMARY KEY (device_id, entity, record_uuid), '
        f'CONSTRAINT fk_device_holdings_device FOREIGN KEY (tenant_id, device_id) '
        f'REFERENCES {t(TABLE)} (tenant_id, id))'))
    secure(connection, schema, role)


def upgrade():
    connection = op.get_bind()
    reconcile(connection, connection.execute(text('SELECT current_schema()')).scalar_one(),
              op.get_context().config.attributes.get('runtime_role'))


def downgrade():
    raise RuntimeError('Removing devices signs every phone out; use a reviewed migration')
