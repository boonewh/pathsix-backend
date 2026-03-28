"""
Wipes ALL tenant_id=1 data from the NEW DB (pathsixsolutions_backend).
Tenant 2 and the tenants table are untouched.
Run BEFORE re-running migrate_from_old_crm.py.
"""

import os
import sys
import psycopg2

NEW_DB_URL = os.environ.get("NEW_DATABASE_URL", "")

if not NEW_DB_URL:
    print("ERROR: NEW_DATABASE_URL not set.")
    sys.exit(1)

conn = psycopg2.connect(NEW_DB_URL)
conn.autocommit = False
cur = conn.cursor()

try:
    print("Wiping tenant_id=1 from new DB...")

    # user_preferences and user_roles first (FK to users)
    cur.execute("""
        DELETE FROM user_preferences WHERE user_id IN (
            SELECT id FROM users WHERE tenant_id = 1
        )
    """)
    print(f"  user_preferences: {cur.rowcount} deleted")

    cur.execute("""
        DELETE FROM user_roles WHERE user_id IN (
            SELECT id FROM users WHERE tenant_id = 1
        )
    """)
    print(f"  user_roles: {cur.rowcount} deleted")

    # Standalone tenant-scoped tables
    for table in ["activity_logs", "chat_messages", "messages", "interactions", "contacts", "accounts"]:
        cur.execute(f"DELETE FROM {table} WHERE tenant_id = 1")
        print(f"  {table}: {cur.rowcount} deleted")

    # Projects before clients/leads (FK to both)
    cur.execute("DELETE FROM projects WHERE tenant_id = 1")
    print(f"  projects: {cur.rowcount} deleted")

    # Clear source_lead_id on clients before deleting leads (FK constraint)
    cur.execute("UPDATE clients SET source_lead_id = NULL WHERE tenant_id = 1")

    cur.execute("DELETE FROM clients WHERE tenant_id = 1")
    print(f"  clients: {cur.rowcount} deleted")

    cur.execute("DELETE FROM leads WHERE tenant_id = 1")
    print(f"  leads: {cur.rowcount} deleted")

    cur.execute("DELETE FROM users WHERE tenant_id = 1")
    print(f"  users: {cur.rowcount} deleted")

    conn.commit()
    print("\nDone. Tenant 1 wiped. Tenant 2 untouched.")

except Exception as e:
    conn.rollback()
    print(f"\nERROR: {e}")
    print("Rolled back — nothing changed.")
    raise
finally:
    cur.close()
    conn.close()
