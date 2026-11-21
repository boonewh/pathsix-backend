"""The phone's own references for records it creates (mobile roadmap step 3, F-14).

Adds mobile_record_refs (app/services/mobile_records.py). A row is inserted in
the same transaction as the create it describes and is never changed, so the
restricted runtime role may read and insert rows of its own tenant (row
security), and nothing else. Refs are unique per tenant, never globally, so a
ref cannot reveal anything about another tenant. The runtime role is found as
in mobile_change_feed.
"""
from alembic import op
from sqlalchemy import inspect, text

from migrations.versions.mobile_change_feed import runtime_role_for, _unsafe

revision = 'mobile_record_refs'
down_revision = 'mobile_device_auth'
branch_labels = depends_on = None

TABLE = 'mobile_record_refs'


def secure(connection, schema, runtime_role):
    """Grants and row security for mobile_record_refs (also used by the test fixtures)."""
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
        raise RuntimeError('Existing mobile_record_refs policies require review')
    role = q(runtime_role)
    sequence = connection.execute(text('SELECT pg_get_serial_sequence(:table, :column)'),
                                  {'table': table, 'column': 'id'}).scalar_one()
    connection.execute(text(f'REVOKE ALL ON {table} FROM PUBLIC, {role}'))
    connection.execute(text(f'REVOKE ALL ON SEQUENCE {sequence} FROM PUBLIC, {role}'))
    connection.execute(text(f'GRANT SELECT, INSERT ON {table} TO {role}'))
    connection.execute(text(f'GRANT USAGE ON SEQUENCE {sequence} TO {role}'))
    own = "tenant_id = NULLIF(current_setting('crm.tenant_id', true), '')::integer"
    connection.execute(text(f'CREATE POLICY crm_all ON {table} FOR ALL TO PUBLIC USING ({own}) WITH CHECK ({own})'))
    connection.execute(text(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY'))
    connection.execute(text(f'ALTER TABLE {table} FORCE ROW LEVEL SECURITY'))
    if not connection.execute(text(
            "SELECT has_table_privilege(:role, :table, 'SELECT') AND has_table_privilege(:role, :table, 'INSERT')"),
            {'role': runtime_role, 'table': table}).scalar_one():
        raise RuntimeError('Runtime role cannot read and insert mobile_record_refs')
    excess = connection.execute(text(
        "SELECT r.rolname FROM pg_roles r WHERE pg_has_role(:role, r.oid, 'MEMBER') AND ("
        "has_table_privilege(r.oid, :table, 'UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER') "
        "OR has_any_column_privilege(r.oid, :table, 'UPDATE, REFERENCES') "
        "OR has_sequence_privilege(r.oid, :sequence, 'UPDATE'))"),
        {'role': runtime_role, 'table': table, 'sequence': sequence}).scalars().all()
    if excess:
        raise RuntimeError('Runtime role privileges on mobile_record_refs must be SELECT and INSERT only')


def reconcile(connection, schema, runtime_role=None):
    if connection.dialect.name != 'postgresql':
        raise RuntimeError('PostgreSQL required')
    connection.execute(text("SET LOCAL lock_timeout='5s'"))
    connection.execute(text("SET LOCAL statement_timeout='60s'"))
    q = connection.dialect.identifier_preparer.quote
    t = lambda name: f'{q(schema)}.{q(name)}'
    role = runtime_role_for(connection, schema, runtime_role)
    if TABLE in inspect(connection).get_table_names(schema=schema):
        raise RuntimeError('Existing mobile_record_refs table requires review')
    connection.execute(text(
        f'CREATE TABLE {t(TABLE)} (id BIGSERIAL PRIMARY KEY, '
        f'tenant_id integer NOT NULL REFERENCES {t("tenants")} (id), ref uuid NOT NULL, '
        'entity varchar(20) NOT NULL, record_id integer NOT NULL, created_at timestamp NOT NULL, '
        'CONSTRAINT uq_mobile_record_refs_ref UNIQUE (tenant_id, ref))'))
    connection.execute(text(f'CREATE INDEX ix_mobile_record_refs_created ON {t(TABLE)} (created_at)'))
    secure(connection, schema, role)


def upgrade():
    connection = op.get_bind()
    reconcile(connection, connection.execute(text('SELECT current_schema()')).scalar_one(),
              op.get_context().config.attributes.get('runtime_role'))


def downgrade():
    raise RuntimeError('Removing mobile record refs strands offline creates; use a reviewed migration')
