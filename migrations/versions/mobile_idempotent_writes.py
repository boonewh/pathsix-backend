"""Write receipts for Idempotency-Key creates and updates (mobile Phase 0, item 5).

Adds write_receipts (app/services/idempotency.py). A receipt is inserted in the
same transaction as the write it describes and is never changed afterwards, so
the restricted runtime role may read and insert receipts, only for its own
tenant and user (row security), and nothing else. scripts/prune_tombstones.py
removes receipts after 90 days, as the operator. The runtime role is found as
in mobile_change_feed.
"""
from alembic import op
from sqlalchemy import inspect, text

from migrations.versions.mobile_change_feed import runtime_role_for, _unsafe

revision = 'mobile_idempotent_writes'
down_revision = 'mobile_change_feed'
branch_labels = depends_on = None

TABLE = 'write_receipts'


def secure(connection, schema, runtime_role):
    """Grants and row security for write_receipts (also used by the test fixtures)."""
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
        raise RuntimeError('Existing write_receipts policies require review')
    role = q(runtime_role)
    sequence = connection.execute(text('SELECT pg_get_serial_sequence(:table, :column)'),
                                  {'table': table, 'column': 'id'}).scalar_one()
    connection.execute(text(f'REVOKE ALL ON {table} FROM PUBLIC, {role}'))
    connection.execute(text(f'REVOKE ALL ON SEQUENCE {sequence} FROM PUBLIC, {role}'))
    connection.execute(text(f'GRANT SELECT, INSERT ON {table} TO {role}'))
    # Returned fully quoted by PostgreSQL; needed for the BIGSERIAL id.
    connection.execute(text(f'GRANT USAGE ON SEQUENCE {sequence} TO {role}'))
    own = ("tenant_id = NULLIF(current_setting('crm.tenant_id', true), '')::integer "
           "AND user_id = NULLIF(current_setting('crm.user_id', true), '')::integer")
    connection.execute(text(f'CREATE POLICY crm_all ON {table} FOR ALL TO PUBLIC USING ({own}) WITH CHECK ({own})'))
    connection.execute(text(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY'))
    connection.execute(text(f'ALTER TABLE {table} FORCE ROW LEVEL SECURITY'))
    # Effective privileges of the role and every role it can assume: read and
    # insert only; receipts are never updated, deleted or truncated at runtime.
    if not connection.execute(text(
            "SELECT has_table_privilege(:role, :table, 'SELECT') AND has_table_privilege(:role, :table, 'INSERT')"),
            {'role': runtime_role, 'table': table}).scalar_one():
        raise RuntimeError('Runtime role cannot read and insert write_receipts')
    excess = connection.execute(text(
        "SELECT r.rolname FROM pg_roles r WHERE pg_has_role(:role, r.oid, 'MEMBER') AND ("
        "has_table_privilege(r.oid, :table, 'UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER') "
        "OR has_any_column_privilege(r.oid, :table, 'UPDATE, REFERENCES') "
        "OR has_sequence_privilege(r.oid, :sequence, 'UPDATE'))"),
        {'role': runtime_role, 'table': table, 'sequence': sequence}).scalars().all()
    if excess:
        raise RuntimeError('Runtime role privileges on write_receipts must be SELECT and INSERT only')


def reconcile(connection, schema, runtime_role=None):
    if connection.dialect.name != 'postgresql':
        raise RuntimeError('PostgreSQL required')
    connection.execute(text("SET LOCAL lock_timeout='5s'"))
    connection.execute(text("SET LOCAL statement_timeout='60s'"))
    q = connection.dialect.identifier_preparer.quote
    t = lambda name: f'{q(schema)}.{q(name)}'
    role = runtime_role_for(connection, schema, runtime_role)
    if TABLE in inspect(connection).get_table_names(schema=schema):
        raise RuntimeError('Existing write_receipts table requires review')
    connection.execute(text(
        f'CREATE TABLE {t(TABLE)} (id BIGSERIAL PRIMARY KEY, '
        f'tenant_id integer NOT NULL REFERENCES {t("tenants")} (id), user_id integer NOT NULL, '
        'request_key varchar(64) NOT NULL, endpoint varchar(100) NOT NULL, input_hash varchar(64) NOT NULL, '
        'status_code integer NOT NULL, response json NOT NULL, created_at timestamp NOT NULL, '
        'CONSTRAINT uq_write_receipts_key UNIQUE (tenant_id, user_id, request_key))'))
    connection.execute(text(f'CREATE INDEX ix_write_receipts_created ON {t(TABLE)} (created_at)'))
    secure(connection, schema, role)


def upgrade():
    connection = op.get_bind()
    reconcile(connection, connection.execute(text('SELECT current_schema()')).scalar_one(),
              op.get_context().config.attributes.get('runtime_role'))


def downgrade():
    raise RuntimeError('Removing write receipts re-enables duplicate retries; use a reviewed migration')
