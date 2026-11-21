"""Mobile sync for unified contacts (F-14c: contacts-cleanup merged into mobile).

After contacts_unified, a contact can belong to a standalone project, so the
contacts sync_mark trigger is recreated with project_id as an access field.

Changes to a client's, lead's or project's person (its contact) reach phones
through the feed itself: SyncFeedService._changed reads the contact's own
marker. No trigger touches the parent records, so no extra locks are taken.

The trigger arguments are a frozen copy of app/utils/sync_markers.py here.
"""
from alembic import op
from sqlalchemy import text

revision = 'mobile_contacts_sync'
down_revision = 'contacts_unified'
branch_labels = depends_on = None

def reconcile(connection, schema):
    if connection.dialect.name != 'postgresql':
        raise RuntimeError('PostgreSQL required')
    connection.execute(text("SET LOCAL lock_timeout='5s'"))
    q = connection.dialect.identifier_preparer.quote
    table = f'{q(schema)}.{q("contacts")}'
    # Unqualified names resolve in the creating schema; callers set search_path,
    # as for mobile_change_feed.
    connection.execute(text(f'DROP TRIGGER {q("sync_mark_contacts")} ON {table}'))
    connection.execute(text(
        f'CREATE TRIGGER {q("sync_mark_contacts")} BEFORE INSERT OR UPDATE ON {table} FOR EACH ROW '
        "EXECUTE FUNCTION sync_mark_row('tenant_id', 'client_id', 'lead_id', 'project_id', 'deleted_at')"))
    # contacts_unified skipped records whose old person columns held only phone
    # labels, so no contact marker exists for the feed to follow, yet their
    # projected labels changed (now null). Touch those records (SET id = id fires
    # sync_mark_row) so existing cursors receive them. Reads the frozen columns.
    t = lambda name: f'{q(schema)}.{q(name)}'
    for parent, key in (('clients', 'client_id'), ('leads', 'lead_id')):
        connection.execute(text(
            f'UPDATE {t(parent)} r SET id = r.id '
            f'WHERE (r.phone_label IS NOT NULL OR r.secondary_phone_label IS NOT NULL) '
            f'AND NOT EXISTS (SELECT 1 FROM {t("contacts")} c WHERE c.{key} = r.id AND c.is_primary)'))
    connection.execute(text(
        f'UPDATE {t("projects")} p SET id = p.id '
        f'WHERE p.primary_contact_phone_label IS NOT NULL AND p.primary_contact_id IS NULL'))


def upgrade():
    connection = op.get_bind()
    reconcile(connection, connection.execute(text('SELECT current_schema()')).scalar_one())


def downgrade():
    raise RuntimeError('Restoring the old contacts trigger hides project-contact access changes; use a reviewed migration')
