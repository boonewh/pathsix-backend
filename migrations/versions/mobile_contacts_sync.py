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


def upgrade():
    connection = op.get_bind()
    reconcile(connection, connection.execute(text('SELECT current_schema()')).scalar_one())


def downgrade():
    raise RuntimeError('Restoring the old contacts trigger hides project-contact access changes; use a reviewed migration')
