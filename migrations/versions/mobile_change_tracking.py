"""Change tracking for syncable CRM records (mobile Phase 0, item 2).

Every syncable table gets a non-null created_at and updated_at, and interactions
get created_by. A change feed can then rely on updated_at being set on every row.

Backfill is deliberately conservative: a missing created_at becomes the migration
time, updated_at starts equal to created_at where it was never set, and legacy
interactions keep created_by NULL. activity_logs is not used as evidence: the
old CRM importer copied its entity_ids without remapping them, so an entry can
describe a different record, and a wrong time or creator is worse than an
honest "unknown". A later, reviewed backfill can add evidence if it is found.
"""
from alembic import op
from sqlalchemy import text, inspect

revision = 'mobile_change_tracking'
down_revision = 'mobile_stable_ids'
branch_labels = depends_on = None

SYNCED = ('clients', 'leads', 'projects', 'contacts', 'interactions', 'accounts')
ADD_CREATED_AT = ('interactions', 'accounts')
ADD_UPDATED_AT = ('contacts', 'interactions', 'accounts')
UTC_NOW = "timezone('utc', now())"


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

    # Take the strongest lock the ALTERs need up front: a writer committing
    # between backfill and SET NOT NULL would leave NULLs, and upgrading a weaker
    # lock later can deadlock with a reader that starts writing.
    connection.execute(text(f'LOCK TABLE {", ".join(t(n) for n in SYNCED)} IN ACCESS EXCLUSIVE MODE'))
    for name in ADD_CREATED_AT:
        connection.execute(text(f'ALTER TABLE {t(name)} ADD COLUMN created_at timestamp'))
    for name in ADD_UPDATED_AT:
        connection.execute(text(f'ALTER TABLE {t(name)} ADD COLUMN updated_at timestamp'))
    connection.execute(text(f'ALTER TABLE {t("interactions")} ADD COLUMN created_by integer'))

    for name in SYNCED:
        connection.execute(text(f'UPDATE {t(name)} SET created_at = {UTC_NOW} WHERE created_at IS NULL'))
        connection.execute(text(f'UPDATE {t(name)} SET updated_at = created_at WHERE updated_at IS NULL'))
        connection.execute(text(
            f'ALTER TABLE {t(name)} ALTER COLUMN created_at SET DEFAULT {UTC_NOW}, '
            f'ALTER COLUMN created_at SET NOT NULL, '
            f'ALTER COLUMN updated_at SET DEFAULT {UTC_NOW}, '
            f'ALTER COLUMN updated_at SET NOT NULL'))

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
