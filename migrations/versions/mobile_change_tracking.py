"""Change tracking for syncable CRM records (mobile Phase 0, item 2).

Every syncable table gets a non-null created_at and updated_at, and interactions
get created_by. A change feed can then rely on updated_at being set on every row.

Backfill, in order of preference: the record's 'created' activity-log entry in
the same tenant (real evidence of when and by whom), then the migration time.
An entry counts only if its description still names this record the way
app.utils.sales_audit writes it: the old CRM importer copied activity entity_ids
without remapping them, so an id match alone can point at another record. A
mismatch falls back safely. updated_at starts equal to created_at where it was
never set. Nothing invents a creator: created_by stays NULL without evidence.
"""
from alembic import op
from sqlalchemy import text, inspect

revision = 'mobile_change_tracking'
down_revision = 'mobile_stable_ids'
branch_labels = depends_on = None

SYNCED = ('clients', 'leads', 'projects', 'contacts', 'interactions', 'accounts')
# activity_logs.entity_type is the model class name lowercased (app.utils.sales_audit).
ENTITY = {'clients': 'client', 'leads': 'lead', 'projects': 'project', 'contacts': 'contact',
          'interactions': 'interaction', 'accounts': 'account'}
ADD_CREATED_AT = ('interactions', 'accounts')
ADD_UPDATED_AT = ('contacts', 'interactions', 'accounts')
# The attribute sales_audit.record_change uses as the activity description, and
# the fallback it writes when that attribute is empty.
NAME = {'clients': ('name', 'Client'), 'leads': ('name', 'Lead'), 'projects': ('project_name', 'Project'),
        'contacts': ('first_name', 'Contact'), 'interactions': ('summary', 'Interaction'),
        'accounts': ('account_name', 'Account')}
UTC_NOW = "timezone('utc', now())"


def described(name, alias):
    column, label = NAME[name]
    return f"COALESCE(NULLIF({alias}.{column}, ''), '{label} #' || {alias}.id)"


def reconcile(connection, schema):
    if connection.dialect.name != 'postgresql':
        raise RuntimeError('PostgreSQL required')
    connection.execute(text("SET LOCAL lock_timeout='5s'"))
    connection.execute(text("SET LOCAL statement_timeout='60s'"))
    q = connection.dialect.identifier_preparer.quote
    t = lambda name: f'{q(schema)}.{q(name)}'
    inspector = inspect(connection)
    columns = {name: {c['name'] for c in inspector.get_columns(name, schema=schema)} for name in SYNCED}
    for name in ADD_CREATED_AT:
        if 'created_at' in columns[name]:
            raise RuntimeError(f'Existing {name}.created_at requires review')
    for name in ADD_UPDATED_AT:
        if 'updated_at' in columns[name]:
            raise RuntimeError(f'Existing {name}.updated_at requires review')
    if 'created_by' in columns['interactions']:
        raise RuntimeError('Existing interactions.created_by requires review')

    # Writers that commit between the backfill and SET NOT NULL would leave NULLs
    # behind; block writes (not reads) on all six tables for the whole migration.
    connection.execute(text(f'LOCK TABLE {", ".join(t(n) for n in SYNCED)} IN SHARE ROW EXCLUSIVE MODE'))
    for name in ADD_CREATED_AT:
        connection.execute(text(f'ALTER TABLE {t(name)} ADD COLUMN created_at timestamp'))
    for name in ADD_UPDATED_AT:
        connection.execute(text(f'ALTER TABLE {t(name)} ADD COLUMN updated_at timestamp'))
    connection.execute(text(f'ALTER TABLE {t("interactions")} ADD COLUMN created_by integer'))

    for name in SYNCED:
        # Earliest same-tenant 'created' entry for this record, if any.
        connection.execute(text(
            f'UPDATE {t(name)} r SET created_at = a.at FROM ('
            f'  SELECT l.tenant_id, l.entity_id, min(l."timestamp") AS at FROM {t("activity_logs")} l'
            f'  JOIN {t(name)} x ON x.tenant_id = l.tenant_id AND x.id = l.entity_id'
            f"  WHERE l.entity_type = :entity AND l.action = 'created' AND l.description = {described(name, 'x')}"
            f'  GROUP BY l.tenant_id, l.entity_id'
            f') a WHERE r.created_at IS NULL AND a.tenant_id = r.tenant_id AND a.entity_id = r.id'),
            {'entity': ENTITY[name]})
        connection.execute(text(f'UPDATE {t(name)} SET created_at = {UTC_NOW} WHERE created_at IS NULL'))
        connection.execute(text(f'UPDATE {t(name)} SET updated_at = created_at WHERE updated_at IS NULL'))
        connection.execute(text(
            f'ALTER TABLE {t(name)} ALTER COLUMN created_at SET DEFAULT {UTC_NOW}, '
            f'ALTER COLUMN created_at SET NOT NULL, '
            f'ALTER COLUMN updated_at SET DEFAULT {UTC_NOW}, '
            f'ALTER COLUMN updated_at SET NOT NULL'))

    # Creator of the earliest same-tenant 'created' entry, only if that user is in the tenant.
    connection.execute(text(
        f'UPDATE {t("interactions")} i SET created_by = a.user_id FROM ('
        f'  SELECT DISTINCT ON (l.tenant_id, l.entity_id) l.tenant_id, l.entity_id, l.user_id'
        f'  FROM {t("activity_logs")} l JOIN {t("interactions")} x ON x.tenant_id = l.tenant_id AND x.id = l.entity_id'
        f"  WHERE l.entity_type = 'interaction' AND l.action = 'created' AND l.description = {described('interactions', 'x')}"
        f'  ORDER BY l.tenant_id, l.entity_id, l."timestamp", l.id'
        f') a JOIN {t("users")} u ON u.id = a.user_id AND u.tenant_id = a.tenant_id '
        f'WHERE a.tenant_id = i.tenant_id AND a.entity_id = i.id'))
    connection.execute(text(
        f'ALTER TABLE {t("interactions")} '
        f'ADD CONSTRAINT {q("interactions_created_by_fkey")} FOREIGN KEY (created_by) REFERENCES {t("users")} (id), '
        # Same-tenant backstop, as tenant_relationships does for every user reference.
        f'ADD CONSTRAINT {q("fk_interactions_created_by_same_tenant")} FOREIGN KEY (tenant_id, created_by) '
        f'REFERENCES {t("users")} (tenant_id, id)'))
    # Grants on these tables are table-level, so the restricted runtime role
    # already covers the new columns.


def upgrade():
    connection = op.get_bind()
    reconcile(connection, connection.execute(text('SELECT current_schema()')).scalar_one())


def downgrade():
    raise RuntimeError('Preserve change-tracking history devices may rely on; use a reviewed migration')
