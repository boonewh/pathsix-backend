# CRM Migration Guide
Migrating client data from the original PathSix CRM into the new multi-tenant CRM.

---

## Overview

The original CRM (`pathsix-backend` on Fly.io) holds ASFI's data as tenant 1.
The new CRM (`pathsixsolutions-backend`) is the multi-tenant replacement.
This migration copies all of ASFI's data from the old DB into the new one
without touching or disrupting the old system at any point.

**The old CRM keeps running normally throughout the entire process.**

---

## Files

| File | Purpose |
|------|---------|
| `migrate_from_old_crm.py` | Main migration script |
| `wipe_tenant1_new_db.py` | Cleans target tenant from new DB before re-running |

---

## Step 1 — Take a backup (optional but recommended)

```bash
flyctl ssh console -a pathsix-db
pg_dump $DATABASE_URL > old_crm_backup_$(date +%Y%m%d).sql
```

---

## Step 2 — Get both connection strings

**Old DB:**
```bash
flyctl ssh console -a pathsix-backend
echo $DATABASE_URL
```

**New DB:**
```bash
flyctl ssh console -a pathsixsolutions-backend
echo $DATABASE_URL
```

Save both strings — you'll need them in Step 5.

---

## Step 3 — Wipe the target tenant (if re-running)

If this tenant has already been migrated before (test run, partial run, etc.),
wipe it first so you start clean. **Tenant 2 and all other tenants are untouched.**

```bash
flyctl ssh console -a pathsixsolutions-backend

NEW_DATABASE_URL="<new db url>" \
/venv/bin/python wipe_tenant1_new_db.py
```

Skip this step if the target tenant has never been migrated before.

---

## Step 4 — Deploy latest scripts

```bash
flyctl deploy -a pathsixsolutions-backend
```

This ensures the migration scripts on the server match what's in the repo.

---

## Step 5 — Run the migration

```bash
flyctl ssh console -a pathsixsolutions-backend

OLD_DATABASE_URL="<old db url>" \
NEW_DATABASE_URL="<new db url>" \
/venv/bin/python migrate_from_old_crm.py
```

The script will print progress as it goes, then a verification table at the end.

---

## Step 6 — Verify

The script prints a count comparison between old and new for every table.
All rows should show `✓`. Example of a clean run:

```
── Verification ─────────────────────────────────────────────
Table                     Old      New  Match
----------------------------------------------
users                      10       10      ✓
leads                    1093     1093      ✓
clients                   126      126      ✓
...
----------------------------------------------
All counts match. Migration looks good!
```

If any row shows `✗ MISMATCH`, do not go live. See Troubleshooting below.

---

## Step 7 — Smoke test the new CRM

- Log in as an ASFI user (passwords carry over from old CRM — same credentials)
- Open a client record and confirm interactions and projects are attached
- Open a lead and confirm status and history look right
- Check the Reports page for basic sanity

---

## Going Live

Once verified:

1. Point ASFI users to the new CRM URL
2. Tell them their login credentials are the same as before
3. Keep the old CRM running in read-only mode for a few days as a safety net
4. After confidence period, the old CRM can be shut down

---

## Troubleshooting

**"Transaction rolled back" error**
The new DB is unchanged. Fix the error and re-run. No cleanup needed.

**Counts mismatch after migration**
Run the wipe script, then re-run the migration from scratch.

**SSH session hangs mid-run**
Close the terminal. The migration runs in a single transaction — if it didn't
print "Committed successfully", nothing was written. Re-run cleanly.

**"can't open file migrate_from_old_crm.py"**
The scripts aren't deployed yet. Run `flyctl deploy -a pathsixsolutions-backend` first.

**User can't log in after migration**
Password hashes are copied from the old DB. If a user's password wasn't set
in the old CRM, use the admin panel or a password reset to fix it.

---

## Notes

- The migration does **not** preserve original record IDs. New IDs are assigned
  by Postgres to avoid collisions with other tenants' data. All relationships
  (client↔interactions, lead↔projects, etc.) are correctly rewritten via
  an in-memory ID map built during migration.
- Re-running is safe — always wipe first, then migrate fresh.
- `subscriptions`, `backups`, and `backup_restores` are not migrated
  (they don't exist in the old CRM).
