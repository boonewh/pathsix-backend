"""Change-feed markers and permanent-delete records (mobile Phase 0, item 4).

Adds sync_change_xid / sync_access_xid to the six sync tables, maintained by
triggers (app/utils/sync_markers.py), and sync_purges, written by an AFTER
DELETE trigger and kept for 90 days by scripts/prune_tombstones.py. Existing
rows get the migration's xid as their change marker and 0 as their access
marker: every device starts with a full sync, so no earlier history is needed.

The restricted runtime role may only read sync_purges; the SECURITY DEFINER
delete trigger writes it, and update triggers forbid changing a row's tenant or
uuid. The role comes from the `runtime_role` Alembic attribute when an operator
script supplies one (it must hold INSERT on clients); otherwise it is the single
non-owner role directly granted INSERT on clients, checked like the isolation
contract checks it, so the documented `alembic upgrade head` keeps working.
"""
from alembic import op
from sqlalchemy import inspect, text

from app.utils.sync_markers import (FUNCTIONS_SQL, MARK_FUNCTION, PURGE_FUNCTION, PURGE_TABLE,
                                    SYNC_TABLES, trigger_sql)

revision = 'mobile_change_feed'
down_revision = 'mobile_tombstones'
branch_labels = depends_on = None

MARKERS = ('sync_change_xid', 'sync_access_xid')


def _unsafe(connection, role):
    """Same role checks as ops/isolation_contract.py, including inherited elevation."""
    row = connection.execute(text(
        'SELECT rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb FROM pg_roles WHERE rolname=:role'),
        {'role': role}).first()
    if row is None or row[0]:
        return True
    return connection.execute(text(
        'SELECT EXISTS (SELECT 1 FROM pg_roles WHERE (rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb) '
        "AND pg_has_role(:role, oid, 'MEMBER'))"), {'role': role}).scalar_one()


def runtime_role_for(connection, schema, supplied=None):
    """The supplied role, or the single restricted non-owner role with INSERT on clients."""
    # Read the ACL directly: information_schema hides grants the caller is not party to.
    grantees = set(connection.execute(text(
        'SELECT r.rolname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace '
        'CROSS JOIN LATERAL aclexplode(c.relacl) a JOIN pg_roles r ON r.oid=a.grantee '
        "WHERE n.nspname=:schema AND c.relname='clients' AND a.privilege_type='INSERT' "
        'AND a.grantee <> c.relowner'), {'schema': schema}).scalars())
    if supplied:
        if supplied not in grantees:
            raise RuntimeError('Supplied runtime role is not the role granted on clients')
        role = supplied
    else:
        if len(grantees) != 1:
            raise RuntimeError('Cannot identify a single runtime role; supply runtime_role')
        role = grantees.pop()
    if _unsafe(connection, role):
        raise RuntimeError('Expected a restricted runtime role')
    return role


def secure(connection, schema, runtime_role):
    """Grants and row security for sync_purges (also used by the test fixtures)."""
    if connection.dialect.name != 'postgresql' or not runtime_role:
        raise RuntimeError('PostgreSQL and explicit runtime role required')
    q = connection.dialect.identifier_preparer.quote
    table = f'{q(schema)}.{q(PURGE_TABLE)}'
    owner = connection.execute(text(
        'SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')).scalar_one()
    if not owner or _unsafe(connection, runtime_role):
        raise RuntimeError('Expected operator and restricted runtime role')
    if connection.execute(text('SELECT 1 FROM pg_policies WHERE schemaname=:schema AND tablename=:table'),
                          {'schema': schema, 'table': PURGE_TABLE}).first():
        raise RuntimeError('Existing sync_purges policies require review')
    role = q(runtime_role)
    sequence = connection.execute(text('SELECT pg_get_serial_sequence(:table, :column)'),
                                  {'table': table, 'column': 'id'}).scalar_one()
    # Read only: records are written by the SECURITY DEFINER delete trigger, so
    # the runtime role cannot author, change or remove a purge record.
    connection.execute(text(f'REVOKE ALL ON {table} FROM PUBLIC, {role}'))
    connection.execute(text(f'REVOKE ALL ON SEQUENCE {sequence} FROM PUBLIC, {role}'))
    connection.execute(text(f'GRANT SELECT ON {table} TO {role}'))
    own = "tenant_id = NULLIF(current_setting('crm.tenant_id', true), '')::integer"
    connection.execute(text(f'CREATE POLICY crm_all ON {table} FOR ALL TO PUBLIC USING ({own}) WITH CHECK ({own})'))
    connection.execute(text(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY'))
    connection.execute(text(f'ALTER TABLE {table} FORCE ROW LEVEL SECURITY'))
    # Effective privileges, including any inherited through other roles.
    effective = connection.execute(text(
        "SELECT has_table_privilege(:role, :table, 'SELECT'), "
        "has_table_privilege(:role, :table, 'INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER'), "
        "has_sequence_privilege(:role, :sequence, 'USAGE, UPDATE')"),
        {'role': runtime_role, 'table': table, 'sequence': sequence}).one()
    if tuple(effective) != (True, False, False):
        raise RuntimeError('Runtime role privileges on sync_purges must be SELECT only')


def reconcile(connection, schema, runtime_role=None):
    if connection.dialect.name != 'postgresql':
        raise RuntimeError('PostgreSQL required')
    connection.execute(text("SET LOCAL lock_timeout='5s'"))
    connection.execute(text("SET LOCAL statement_timeout='60s'"))
    q = connection.dialect.identifier_preparer.quote
    t = lambda name: f'{q(schema)}.{q(name)}'
    # Lock first so the catalog state checked below is the state altered.
    connection.execute(text(f'LOCK TABLE {", ".join(t(n) for n in SYNC_TABLES)} IN ACCESS EXCLUSIVE MODE'))
    role = runtime_role_for(connection, schema, runtime_role)
    inspector = inspect(connection)
    if PURGE_TABLE in inspector.get_table_names(schema=schema):
        raise RuntimeError('Existing sync_purges table requires review')
    for name in SYNC_TABLES:
        if {c['name'] for c in inspector.get_columns(name, schema=schema)} & set(MARKERS):
            raise RuntimeError(f'Existing {name} sync markers require review')
    if connection.execute(text(
            'SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace '
            'WHERE n.nspname=:schema AND p.proname IN (:mark, :purge)'),
            {'schema': schema, 'mark': MARK_FUNCTION, 'purge': PURGE_FUNCTION}).first():
        raise RuntimeError('Existing sync functions require review')

    connection.execute(text(
        f'CREATE TABLE {t(PURGE_TABLE)} (id BIGSERIAL PRIMARY KEY, tenant_id integer NOT NULL, '
        'entity varchar(20) NOT NULL, record_uuid uuid NOT NULL, purged_xid bigint NOT NULL, '
        'purged_at timestamp NOT NULL)'))
    connection.execute(text(f'CREATE INDEX ix_sync_purges_tenant_xid ON {t(PURGE_TABLE)} (tenant_id, purged_xid)'))
    secure(connection, schema, role)

    # Unqualified, the functions are created in current_schema(), which is `schema`
    # (upgrade passes it; callers must set search_path), and the triggers resolve them there.
    if connection.execute(text('SELECT current_schema()')).scalar_one() != schema:
        raise RuntimeError('Migration schema must be the current schema')
    connection.execute(text(FUNCTIONS_SQL))
    for name in SYNC_TABLES:
        connection.execute(text(
            f'ALTER TABLE {t(name)} ADD COLUMN sync_change_xid bigint NOT NULL DEFAULT 0, '
            'ADD COLUMN sync_access_xid bigint NOT NULL DEFAULT 0'))
        # Before the trigger exists, so this backfill is not itself an access change.
        connection.execute(text(f'UPDATE {t(name)} SET sync_change_xid = pg_current_xact_id()::text::bigint'))
        for marker in ('change', 'access'):
            connection.execute(text(
                f'CREATE INDEX {q(f"ix_{name}_tenant_sync_{marker}")} ON {t(name)} (tenant_id, sync_{marker}_xid)'))
        connection.execute(text(trigger_sql(name, quote=q, qualified=t(name))))


def upgrade():
    connection = op.get_bind()
    reconcile(connection, connection.execute(text('SELECT current_schema()')).scalar_one(),
              op.get_context().config.attributes.get('runtime_role'))


def downgrade():
    raise RuntimeError('Removing change markers loses sync history; use a reviewed migration')
