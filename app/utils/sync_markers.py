"""Change markers and permanent-delete records for the mobile change feed.

Each sync table carries two transaction ids, maintained by a PostgreSQL trigger so
that ORM, Core, raw SQL, imports and scripts are all covered:

- sync_change_xid: the transaction that last inserted or updated the row.
- sync_access_xid: the transaction that last changed a field deciding who may see
  the row (ACCESS_FIELDS), or 0 if none has since it was inserted. A new row
  cannot already be on a device, so an insert never counts as an access change.

Values written by callers are always overwritten. An AFTER DELETE trigger writes
one sync_purges row per deleted row, so a device learns about permanent deletes
(PurgeService, scripts/prune_tombstones.py, repair scripts) for the 90-day window.

The same DDL is used by the mobile_change_feed migration and, through
install_sync_ddl, by Base.metadata.create_all on PostgreSQL, so model-built test
schemas behave like migrated ones. SQLite gets the columns (default 0) only.
"""
from sqlalchemy import DDL, event

SYNC_TABLES = ('clients', 'leads', 'projects', 'accounts', 'contacts', 'interactions')

ACCESS_FIELDS = {
    'clients': ('tenant_id', 'created_by', 'assigned_to', 'deleted_at'),
    'leads': ('tenant_id', 'created_by', 'assigned_to', 'deleted_at'),
    'projects': ('tenant_id', 'created_by', 'assigned_to', 'client_id', 'lead_id',
                 'deleted_at', 'archived_at'),
    'accounts': ('tenant_id', 'client_id', 'deleted_at'),
    'contacts': ('tenant_id', 'client_id', 'lead_id', 'deleted_at'),
    'interactions': ('tenant_id', 'client_id', 'lead_id', 'project_id', 'deleted_at'),
}

MARK_FUNCTION = 'sync_mark_row'
PURGE_FUNCTION = 'sync_record_purge'
PURGE_TABLE = 'sync_purges'

# Unqualified names resolve in the creating schema (current_schema). The purge
# insert names the table's own schema, so it never depends on a caller's search_path.
FUNCTIONS_SQL = f"""
CREATE OR REPLACE FUNCTION {MARK_FUNCTION}() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    current_xid bigint := pg_current_xact_id()::text::bigint;
    new_row jsonb;
    old_row jsonb;
    field text;
BEGIN
    NEW.sync_change_xid := current_xid;
    IF TG_OP = 'INSERT' THEN
        NEW.sync_access_xid := 0;
        RETURN NEW;
    END IF;
    NEW.sync_access_xid := OLD.sync_access_xid;
    new_row := to_jsonb(NEW);
    old_row := to_jsonb(OLD);
    FOREACH field IN ARRAY TG_ARGV LOOP
        IF new_row -> field IS DISTINCT FROM old_row -> field THEN
            NEW.sync_access_xid := current_xid;
            EXIT;
        END IF;
    END LOOP;
    RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION {PURGE_FUNCTION}() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    EXECUTE format('INSERT INTO %I.{PURGE_TABLE} (tenant_id, entity, record_uuid, purged_xid, purged_at) '
                   'VALUES ($1, $2, $3, pg_current_xact_id()::text::bigint, timezone(''utc'', now()))',
                   TG_TABLE_SCHEMA)
        USING OLD.tenant_id, TG_TABLE_NAME, OLD.uuid;
    RETURN OLD;
END
$$;
"""


def trigger_sql(table, quote=lambda name: f'"{name}"', qualified=None):
    """Both triggers for one table. `qualified` is the schema-qualified table name."""
    target = qualified or quote(table)
    fields = ', '.join(f"'{field}'" for field in ACCESS_FIELDS[table])
    return (
        f'CREATE TRIGGER {quote("sync_mark_" + table)} BEFORE INSERT OR UPDATE ON {target} '
        f'FOR EACH ROW EXECUTE FUNCTION {MARK_FUNCTION}({fields});\n'
        f'CREATE TRIGGER {quote("sync_purge_" + table)} AFTER DELETE ON {target} '
        f'FOR EACH ROW EXECUTE FUNCTION {PURGE_FUNCTION}();\n'
    )


def install_sync_ddl(metadata):
    """Attach the functions and triggers to create_all (PostgreSQL only)."""
    if metadata.info.get('sync_ddl_installed'):
        return
    metadata.info['sync_ddl_installed'] = True
    tables = metadata.tables
    # Functions first: they must exist before any table's trigger references them,
    # and the purge function needs sync_purges only when it fires.
    # DDL applies %-formatting to its statement; double the literal percents.
    event.listen(metadata, 'before_create',
                 DDL(FUNCTIONS_SQL.replace('%', '%%')).execute_if(dialect='postgresql'))
    for name in SYNC_TABLES:
        event.listen(tables[name], 'after_create',
                     DDL(trigger_sql(name, qualified='%(fullname)s')).execute_if(dialect='postgresql'))
