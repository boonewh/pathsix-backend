"""Tombstones for contacts, accounts and interactions (mobile Phase 0, item 3).

Deleting one of these records now sets deleted_at/deleted_by instead of removing
the row, so offline devices can learn about the deletion. ORM reads hide
tombstones (app/utils/tombstones.py documents the exact coverage) and scripts/prune_tombstones.py removes
them after 90 days. Existing rows are all live; nothing is backfilled.

accounts.account_number was unique across every row. It becomes unique among
live accounts only, so a deleted account's number can be reused as before.
"""
from alembic import op
from sqlalchemy import text, inspect

revision = 'mobile_tombstones'
down_revision = 'mobile_change_tracking'
branch_labels = depends_on = None

TABLES = ('contacts', 'accounts', 'interactions')
LIVE_NUMBER_INDEX = 'uq_accounts_account_number_live'


def reconcile(connection, schema):
    if connection.dialect.name != 'postgresql':
        raise RuntimeError('PostgreSQL required')
    connection.execute(text("SET LOCAL lock_timeout='5s'"))
    connection.execute(text("SET LOCAL statement_timeout='60s'"))
    q = connection.dialect.identifier_preparer.quote
    t = lambda name: f'{q(schema)}.{q(name)}'
    # Lock first so the catalog state checked below is the state altered. ADD
    # COLUMN takes ACCESS EXCLUSIVE anyway; taking it for all three up front means
    # no writer slips in between steps and no lock upgrade can deadlock.
    connection.execute(text(f'LOCK TABLE {", ".join(t(n) for n in TABLES)} IN ACCESS EXCLUSIVE MODE'))
    inspector = inspect(connection)
    for name in TABLES:
        columns = {c['name'] for c in inspector.get_columns(name, schema=schema)}
        if columns & {'deleted_at', 'deleted_by'}:
            raise RuntimeError(f'Existing {name} tombstone columns require review')

    constraints = [c['name'] for c in inspector.get_unique_constraints('accounts', schema=schema)
                   if c['column_names'] == ['account_number']]
    # PostgreSQL reflection also lists a constraint's backing index; skip those.
    indexes = [i for i in inspector.get_indexes('accounts', schema=schema)
               if i['unique'] and i['column_names'] == ['account_number']
               and not i.get('duplicates_constraint')]
    if (len(constraints) + len(indexes) != 1
            or any(i['name'] == LIVE_NUMBER_INDEX or i.get('dialect_options', {}).get('postgresql_where')
                   for i in indexes)):
        raise RuntimeError('Expected exactly one unconditional unique rule on accounts.account_number; review it first')
    for name in TABLES:
        connection.execute(text(
            f'ALTER TABLE {t(name)} ADD COLUMN deleted_at timestamp, ADD COLUMN deleted_by integer, '
            f'ADD CONSTRAINT {q(name + "_deleted_by_fkey")} FOREIGN KEY (deleted_by) REFERENCES {t("users")} (id), '
            # Same-tenant backstop, as tenant_relationships does for every user reference.
            f'ADD CONSTRAINT {q("fk_" + name + "_deleted_by_same_tenant")} FOREIGN KEY (tenant_id, deleted_by) '
            f'REFERENCES {t("users")} (tenant_id, id)'))

    if constraints:
        connection.execute(text(f'ALTER TABLE {t("accounts")} DROP CONSTRAINT {q(constraints[0])}'))
    else:
        connection.execute(text(f'DROP INDEX {q(schema)}.{q(indexes[0]["name"])}'))
    connection.execute(text(
        f'CREATE UNIQUE INDEX {q(LIVE_NUMBER_INDEX)} ON {t("accounts")} (account_number) '
        f'WHERE deleted_at IS NULL'))
    # Grants on these tables are table-level, so the restricted runtime role
    # already covers the new columns.


def upgrade():
    connection = op.get_bind()
    reconcile(connection, connection.execute(text('SELECT current_schema()')).scalar_one())


def downgrade():
    raise RuntimeError('Tombstones may be the only record of a deletion; use a reviewed migration')
