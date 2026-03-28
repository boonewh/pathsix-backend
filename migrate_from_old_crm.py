"""
Migration script: pathsix_backend (old CRM) → pathsixsolutions_backend (new CRM)

PURPOSE
-------
Migrates one tenant's data from the original PathSix CRM (pathsix-backend on Fly.io)
into the new multi-tenant CRM (pathsixsolutions-backend on Fly.io).

SAFE TO RUN
-----------
- The OLD database is never written to. Read-only.
- Everything on the new side runs in a single transaction. If anything fails,
  the entire migration rolls back and the new DB is left unchanged.
- Safe to re-run after a failed attempt with no cleanup needed.

WHY WE DON'T PRESERVE OLD IDs
------------------------------
The new CRM is multi-tenant and may already have data from other tenants.
Those tenants' records have auto-incremented IDs that overlap with the old CRM's IDs.
Trying to insert old IDs directly causes silent data loss (ON CONFLICT DO NOTHING)
or hard errors (duplicate key). Instead, we let Postgres assign new IDs and
build an in-memory mapping (old_id → new_id) to correctly rewrite all
foreign key references (e.g. lead_id on interactions, client_id on projects).

BEFORE YOU RUN
--------------
1. If the target tenant already has data in the new DB from a previous test migration,
   wipe it first:
       /venv/bin/python wipe_tenant1_new_db.py
   (Edit TENANT_ID in that script if migrating a different tenant.)

2. Get connection strings:
       flyctl ssh console -a <old-app-name>
       echo $DATABASE_URL   ← copy this as OLD_DATABASE_URL

       flyctl ssh console -a pathsixsolutions-backend
       echo $DATABASE_URL   ← copy this as NEW_DATABASE_URL

HOW TO RUN
----------
    flyctl ssh console -a pathsixsolutions-backend

    OLD_DATABASE_URL="postgresql://user:pass@old-db.flycast:5432/old_db" \\
    NEW_DATABASE_URL="postgres://user:pass@new-db.flycast:5432/new_db?sslmode=disable" \\
    /venv/bin/python migrate_from_old_crm.py

WHAT GETS MIGRATED
------------------
  users, roles, user_roles
  leads, clients, accounts, contacts
  projects, interactions
  activity_logs, chat_messages, messages
  user_preferences

  NOT migrated (new CRM concepts that don't exist in old CRM):
  subscriptions, backups, backup_restores

AFTER MIGRATION
---------------
- Check the verification table printed at the end. All counts should match.
- Log into the new CRM and spot-check a client, lead, and their interactions.
- User passwords are carried over from the old DB (same bcrypt hashes),
  so all existing users can log in with their old passwords.
- The wipe script and this script can be deleted from the server after a
  successful production migration.
"""

import os
import sys
import psycopg2
import psycopg2.extras

OLD_DB_URL = os.environ.get("OLD_DATABASE_URL", "")
NEW_DB_URL = os.environ.get("NEW_DATABASE_URL", "")
TENANT_ID = 1


def connect(url, label):
    if not url:
        print(f"ERROR: {label} URL is empty.")
        sys.exit(1)
    conn = psycopg2.connect(url)
    conn.autocommit = False
    print(f"Connected to {label}")
    return conn


def fetch_all(cur, sql, params=None):
    cur.execute(sql, params or ())
    return cur.fetchall()


def insert_returning_id(cur, sql, params):
    cur.execute(sql + " RETURNING id", params)
    return cur.fetchone()["id"]


# ── Migrate users ─────────────────────────────────────────────────────────────

def migrate_users(old_cur, new_cur):
    """
    Returns mapping: old_user_id → new_user_id
    Skips users whose email already exists in new DB.
    """
    rows = fetch_all(old_cur, """
        SELECT id, tenant_id, email, password_hash, is_active, created_at
        FROM users WHERE tenant_id = %s
    """, (TENANT_ID,))

    user_map = {}
    inserted = skipped = 0

    for row in rows:
        new_cur.execute("SELECT id FROM users WHERE email = %s", (row["email"],))
        existing = new_cur.fetchone()
        if existing:
            user_map[row["id"]] = existing["id"]
            skipped += 1
            continue

        new_id = insert_returning_id(new_cur, """
            INSERT INTO users (tenant_id, email, password_hash, is_active, created_at)
            VALUES (%s, %s, %s, %s, %s)
        """, (row["tenant_id"], row["email"], row["password_hash"], row["is_active"], row["created_at"]))

        user_map[row["id"]] = new_id
        inserted += 1

    print(f"  users: {inserted} inserted, {skipped} mapped to existing — {len(user_map)} total in map")
    return user_map


# ── Migrate roles / user_roles ────────────────────────────────────────────────

def migrate_roles_and_user_roles(old_cur, new_cur, user_map):
    roles = fetch_all(old_cur, "SELECT id, name FROM roles")
    for row in roles:
        new_cur.execute("INSERT INTO roles (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (row["name"],))

    user_roles = fetch_all(old_cur, """
        SELECT ur.user_id, ur.role_id, r.name
        FROM user_roles ur
        JOIN users u ON u.id = ur.user_id
        JOIN roles r ON r.id = ur.role_id
        WHERE u.tenant_id = %s
    """, (TENANT_ID,))

    inserted = 0
    for row in user_roles:
        new_user_id = user_map.get(row["user_id"])
        if not new_user_id:
            continue
        new_cur.execute("SELECT id FROM roles WHERE name = %s", (row["name"],))
        role = new_cur.fetchone()
        if not role:
            continue
        new_cur.execute("""
            INSERT INTO user_roles (user_id, role_id) VALUES (%s, %s)
            ON CONFLICT DO NOTHING
        """, (new_user_id, role["id"]))
        inserted += 1

    print(f"  roles: {len(roles)} upserted, user_roles: {inserted} inserted")


# ── Migrate leads ─────────────────────────────────────────────────────────────

def migrate_leads(old_cur, new_cur, user_map):
    """Returns mapping: old_lead_id → new_lead_id"""
    rows = fetch_all(old_cur, """
        SELECT id, tenant_id, created_by, updated_by, updated_at,
               deleted_at, deleted_by, assigned_to,
               name, contact_person, contact_title, email,
               phone, phone_label, secondary_phone, secondary_phone_label,
               address, city, state, zip, type, notes,
               created_at, lead_status, converted_on
        FROM leads WHERE tenant_id = %s
    """, (TENANT_ID,))

    lead_map = {}
    for row in rows:
        new_id = insert_returning_id(new_cur, """
            INSERT INTO leads (
                tenant_id, created_by, updated_by, updated_at,
                deleted_at, deleted_by, assigned_to,
                name, contact_person, contact_title, email,
                phone, phone_label, secondary_phone, secondary_phone_label,
                address, city, state, zip, type, notes,
                created_at, lead_status, converted_on, lead_source
            ) VALUES (
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL
            )
        """, (
            row["tenant_id"],
            user_map.get(row["created_by"]), user_map.get(row["updated_by"]), row["updated_at"],
            row["deleted_at"], user_map.get(row["deleted_by"]), user_map.get(row["assigned_to"]),
            row["name"], row["contact_person"], row["contact_title"], row["email"],
            row["phone"], row["phone_label"], row["secondary_phone"], row["secondary_phone_label"],
            row["address"], row["city"], row["state"], row["zip"], row["type"], row["notes"],
            row["created_at"], row["lead_status"], row["converted_on"],
        ))
        lead_map[row["id"]] = new_id

    print(f"  leads: {len(lead_map)} inserted")
    return lead_map


# ── Migrate clients ───────────────────────────────────────────────────────────

def migrate_clients(old_cur, new_cur, user_map, lead_map):
    """Returns mapping: old_client_id → new_client_id"""
    rows = fetch_all(old_cur, """
        SELECT id, tenant_id, created_by, updated_by, updated_at,
               deleted_by, assigned_to,
               name, contact_person, contact_title, email,
               phone, phone_label, secondary_phone, secondary_phone_label,
               address, city, state, zip, status, type, notes,
               created_at, deleted_at
        FROM clients WHERE tenant_id = %s
    """, (TENANT_ID,))

    client_map = {}
    for row in rows:
        new_id = insert_returning_id(new_cur, """
            INSERT INTO clients (
                tenant_id, created_by, updated_by, updated_at,
                deleted_by, assigned_to,
                name, contact_person, contact_title, email,
                phone, phone_label, secondary_phone, secondary_phone_label,
                address, city, state, zip, status, type, notes,
                created_at, deleted_at,
                source_lead_id, converted_on
            ) VALUES (
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,NULL
            )
        """, (
            row["tenant_id"],
            user_map.get(row["created_by"]), user_map.get(row["updated_by"]), row["updated_at"],
            user_map.get(row["deleted_by"]), user_map.get(row["assigned_to"]),
            row["name"], row["contact_person"], row["contact_title"], row["email"],
            row["phone"], row["phone_label"], row["secondary_phone"], row["secondary_phone_label"],
            row["address"], row["city"], row["state"], row["zip"],
            row["status"], row["type"], row["notes"],
            row["created_at"], row["deleted_at"],
        ))
        client_map[row["id"]] = new_id

    print(f"  clients: {len(client_map)} inserted")
    return client_map


# ── Migrate accounts ──────────────────────────────────────────────────────────

def migrate_accounts(old_cur, new_cur, client_map):
    rows = fetch_all(old_cur, """
        SELECT id, client_id, tenant_id, account_number, account_name, status, opened_on, notes
        FROM accounts WHERE tenant_id = %s
    """, (TENANT_ID,))

    inserted = 0
    for row in rows:
        new_client_id = client_map.get(row["client_id"])
        if not new_client_id:
            continue
        new_cur.execute("""
            INSERT INTO accounts (client_id, tenant_id, account_number, account_name, status, opened_on, notes)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (account_number) DO NOTHING
        """, (new_client_id, row["tenant_id"], row["account_number"], row["account_name"],
              row["status"], row["opened_on"], row["notes"]))
        inserted += 1

    print(f"  accounts: {inserted} inserted")


# ── Migrate contacts ──────────────────────────────────────────────────────────

def migrate_contacts(old_cur, new_cur, client_map, lead_map):
    rows = fetch_all(old_cur, """
        SELECT id, tenant_id, client_id, lead_id,
               first_name, last_name, title, email,
               phone, phone_label, secondary_phone, secondary_phone_label,
               notes, created_at
        FROM contacts WHERE tenant_id = %s
    """, (TENANT_ID,))

    inserted = 0
    for row in rows:
        new_cur.execute("""
            INSERT INTO contacts (
                tenant_id, client_id, lead_id,
                first_name, last_name, title, email,
                phone, phone_label, secondary_phone, secondary_phone_label,
                notes, created_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            row["tenant_id"],
            client_map.get(row["client_id"]), lead_map.get(row["lead_id"]),
            row["first_name"], row["last_name"], row["title"], row["email"],
            row["phone"], row["phone_label"], row["secondary_phone"], row["secondary_phone_label"],
            row["notes"], row["created_at"],
        ))
        inserted += 1

    print(f"  contacts: {inserted} inserted")


# ── Migrate projects ──────────────────────────────────────────────────────────

def migrate_projects(old_cur, new_cur, client_map, lead_map, user_map):
    """Returns mapping: old_project_id → new_project_id"""
    rows = fetch_all(old_cur, """
        SELECT id, lead_id, client_id, tenant_id,
               project_name, project_description, type,
               primary_contact_name, primary_contact_title,
               primary_contact_email, primary_contact_phone, primary_contact_phone_label,
               notes, project_status, project_start, project_end, project_worth,
               created_at, created_by, updated_at, last_updated_by,
               deleted_at, deleted_by
        FROM projects WHERE tenant_id = %s
    """, (TENANT_ID,))

    project_map = {}
    for row in rows:
        new_id = insert_returning_id(new_cur, """
            INSERT INTO projects (
                lead_id, client_id, tenant_id,
                project_name, project_description, type,
                primary_contact_name, primary_contact_title,
                primary_contact_email, primary_contact_phone, primary_contact_phone_label,
                notes, project_status, project_start, project_end, project_worth,
                created_at, created_by, updated_at, last_updated_by,
                deleted_at, deleted_by, value_type
            ) VALUES (
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'one_time'
            )
        """, (
            lead_map.get(row["lead_id"]), client_map.get(row["client_id"]), row["tenant_id"],
            row["project_name"], row["project_description"], row["type"],
            row["primary_contact_name"], row["primary_contact_title"],
            row["primary_contact_email"], row["primary_contact_phone"], row["primary_contact_phone_label"],
            row["notes"], row["project_status"], row["project_start"], row["project_end"], row["project_worth"],
            row["created_at"], user_map.get(row["created_by"]), row["updated_at"],
            user_map.get(row["last_updated_by"]), row["deleted_at"], user_map.get(row["deleted_by"]),
        ))
        project_map[row["id"]] = new_id

    print(f"  projects: {len(project_map)} inserted")
    return project_map


# ── Migrate interactions ──────────────────────────────────────────────────────

def migrate_interactions(old_cur, new_cur, client_map, lead_map, project_map):
    rows = fetch_all(old_cur, """
        SELECT id, lead_id, client_id, project_id, tenant_id,
               contact_person, email, phone, contact_date,
               outcome, notes, follow_up, followup_status, summary
        FROM interactions WHERE tenant_id = %s
    """, (TENANT_ID,))

    inserted = 0
    for row in rows:
        new_cur.execute("""
            INSERT INTO interactions (
                lead_id, client_id, project_id, tenant_id,
                contact_person, email, phone, contact_date,
                outcome, notes, follow_up, followup_status, summary
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            lead_map.get(row["lead_id"]), client_map.get(row["client_id"]),
            project_map.get(row["project_id"]), row["tenant_id"],
            row["contact_person"], row["email"], row["phone"], row["contact_date"],
            row["outcome"], row["notes"], row["follow_up"], row["followup_status"], row["summary"],
        ))
        inserted += 1

    print(f"  interactions: {inserted} inserted")


# ── Migrate activity logs ─────────────────────────────────────────────────────

def migrate_activity_logs(old_cur, new_cur, user_map):
    rows = fetch_all(old_cur, """
        SELECT id, tenant_id, user_id, action, entity_type, entity_id, timestamp, description
        FROM activity_logs WHERE tenant_id = %s
    """, (TENANT_ID,))

    inserted = 0
    for row in rows:
        new_user_id = user_map.get(row["user_id"])
        if not new_user_id:
            continue
        new_cur.execute("""
            INSERT INTO activity_logs (tenant_id, user_id, action, entity_type, entity_id, timestamp, description)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
        """, (row["tenant_id"], new_user_id, row["action"], row["entity_type"],
              row["entity_id"], row["timestamp"], row["description"]))
        inserted += 1

    print(f"  activity_logs: {inserted} inserted")


# ── Migrate chat messages ─────────────────────────────────────────────────────

def migrate_chat_messages(old_cur, new_cur, user_map, client_map, lead_map):
    rows = fetch_all(old_cur, """
        SELECT id, tenant_id, sender_id, recipient_id, room,
               client_id, lead_id, content, is_read, timestamp
        FROM chat_messages WHERE tenant_id = %s
    """, (TENANT_ID,))

    inserted = 0
    for row in rows:
        new_cur.execute("""
            INSERT INTO chat_messages (tenant_id, sender_id, recipient_id, room, client_id, lead_id, content, is_read, timestamp)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            row["tenant_id"], user_map.get(row["sender_id"]), user_map.get(row["recipient_id"]),
            row["room"], client_map.get(row["client_id"]), lead_map.get(row["lead_id"]),
            row["content"], row["is_read"], row["timestamp"],
        ))
        inserted += 1

    print(f"  chat_messages: {inserted} inserted")


# ── Migrate messages ──────────────────────────────────────────────────────────

def migrate_messages(old_cur, new_cur, user_map):
    rows = fetch_all(old_cur, """
        SELECT id, tenant_id, sender_id, receiver_id, body, sent_at, read
        FROM messages WHERE tenant_id = %s
    """, (TENANT_ID,))

    inserted = 0
    for row in rows:
        new_cur.execute("""
            INSERT INTO messages (tenant_id, sender_id, receiver_id, body, sent_at, read)
            VALUES (%s,%s,%s,%s,%s,%s)
        """, (
            row["tenant_id"], user_map.get(row["sender_id"]), user_map.get(row["receiver_id"]),
            row["body"], row["sent_at"], row["read"],
        ))
        inserted += 1

    print(f"  messages: {inserted} inserted")


# ── Migrate user preferences ──────────────────────────────────────────────────

def migrate_user_preferences(old_cur, new_cur, user_map):
    rows = fetch_all(old_cur, """
        SELECT up.user_id, up.category, up.preference_key, up.preference_value, up.created_at, up.updated_at
        FROM user_preferences up
        JOIN users u ON u.id = up.user_id
        WHERE u.tenant_id = %s
    """, (TENANT_ID,))

    inserted = 0
    for row in rows:
        new_user_id = user_map.get(row["user_id"])
        if not new_user_id:
            continue
        new_cur.execute("""
            INSERT INTO user_preferences (user_id, category, preference_key, preference_value, created_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s)
            ON CONFLICT (user_id, category, preference_key) DO NOTHING
        """, (
            new_user_id, row["category"], row["preference_key"],
            psycopg2.extras.Json(row["preference_value"]), row["created_at"], row["updated_at"],
        ))
        inserted += 1

    print(f"  user_preferences: {inserted} inserted")


# ── Verification ──────────────────────────────────────────────────────────────

def verify(old_cur, new_cur):
    tables = [
        "users", "leads", "clients", "accounts", "contacts",
        "projects", "interactions", "activity_logs", "chat_messages", "messages",
    ]
    print("\n── Verification ─────────────────────────────────────────────")
    print(f"{'Table':<20} {'Old':>8} {'New':>8} {'Match':>6}")
    print("-" * 46)
    all_match = True
    for table in tables:
        old_cur.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE tenant_id = 1")
        old_n = old_cur.fetchone()["n"]
        new_cur.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE tenant_id = 1")
        new_n = new_cur.fetchone()["n"]
        match = "✓" if old_n == new_n else "✗ MISMATCH"
        if old_n != new_n:
            all_match = False
        print(f"{table:<20} {old_n:>8} {new_n:>8} {match:>6}")
    print("-" * 46)
    if all_match:
        print("All counts match. Migration looks good!")
    else:
        print("WARNING: Some counts don't match — review before going live.")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=== PathSix CRM Migration: pathsix_backend → pathsixsolutions_backend ===\n")

    old_conn = connect(OLD_DB_URL, "OLD DB (pathsix_backend)")
    new_conn = connect(NEW_DB_URL, "NEW DB (pathsixsolutions_backend)")

    old_cur = old_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    new_cur = new_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        print("\nMigrating data (tenant_id=1 / ASFI)...\n")

        user_map    = migrate_users(old_cur, new_cur)
        migrate_roles_and_user_roles(old_cur, new_cur, user_map)
        lead_map    = migrate_leads(old_cur, new_cur, user_map)
        client_map  = migrate_clients(old_cur, new_cur, user_map, lead_map)
        migrate_accounts(old_cur, new_cur, client_map)
        migrate_contacts(old_cur, new_cur, client_map, lead_map)
        project_map = migrate_projects(old_cur, new_cur, client_map, lead_map, user_map)
        migrate_interactions(old_cur, new_cur, client_map, lead_map, project_map)
        migrate_activity_logs(old_cur, new_cur, user_map)
        migrate_chat_messages(old_cur, new_cur, user_map, client_map, lead_map)
        migrate_messages(old_cur, new_cur, user_map)
        migrate_user_preferences(old_cur, new_cur, user_map)

        new_conn.commit()
        print("\nCommitted successfully.\n")

        verify(old_cur, new_cur)

    except Exception as e:
        new_conn.rollback()
        print(f"\nERROR: {e}")
        print("Transaction rolled back — new DB is unchanged.")
        raise
    finally:
        old_cur.close()
        new_cur.close()
        old_conn.close()
        new_conn.close()


if __name__ == "__main__":
    main()
