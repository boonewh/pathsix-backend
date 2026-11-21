"""Mobile sync for unified contacts (F-14c: contacts-cleanup merged into mobile).

After contacts_unified, a client's or lead's person fields read its primary
contact and a project points at one contact, so a change to a contact changes
what the feed sends for those records. This migration:

- recreates the contacts sync_mark trigger with project_id as an access field
  (a contact can now belong to a standalone project);
- adds an AFTER trigger on contacts that touches the primary contact's client or
  lead, and every project pointing at the contact (UPDATE ... SET id = id fires
  their own sync_mark_row trigger, so their change markers move).

The SQL is a frozen copy of app/utils/sync_markers.py at this revision.
"""
from alembic import op
from sqlalchemy import text

revision = 'mobile_contacts_sync'
down_revision = 'contacts_unified'
branch_labels = depends_on = None

FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION sync_touch_contact_parents() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP <> 'INSERT' THEN
        IF OLD.is_primary THEN
            UPDATE clients SET id = id WHERE id = OLD.client_id AND tenant_id = OLD.tenant_id;
            UPDATE leads SET id = id WHERE id = OLD.lead_id AND tenant_id = OLD.tenant_id;
        END IF;
        UPDATE projects SET id = id WHERE primary_contact_id = OLD.id AND tenant_id = OLD.tenant_id;
    END IF;
    IF TG_OP <> 'DELETE' AND NEW.is_primary THEN
        UPDATE clients SET id = id WHERE id = NEW.client_id AND tenant_id = NEW.tenant_id;
        UPDATE leads SET id = id WHERE id = NEW.lead_id AND tenant_id = NEW.tenant_id;
    END IF;
    RETURN NULL;
END
$$;
"""


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
    connection.execute(text(FUNCTION_SQL))
    connection.execute(text(
        f'CREATE TRIGGER {q("sync_touch_contact_parents")} AFTER INSERT OR UPDATE OR DELETE ON {table} '
        'FOR EACH ROW EXECUTE FUNCTION sync_touch_contact_parents()'))


def upgrade():
    connection = op.get_bind()
    reconcile(connection, connection.execute(text('SELECT current_schema()')).scalar_one())


def downgrade():
    raise RuntimeError('Removing contact sync triggers leaves devices with stale person fields; use a reviewed migration')
