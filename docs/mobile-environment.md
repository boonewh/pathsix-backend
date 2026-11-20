# Mobile development environment

Created November 17, 2026 at the owner's request, as the place for the mobile
app's backend work. Scope note: this environment and the mobile work it serves
are outside the AI connector MVP scope lock in `docs/mcp-readiness-status.md`.
The owner approved it directly.

## Why it exists separately

Mobile backend work does not go on `staging`. A production hotfix is cut from
`staging` (production currently runs `release-94ffe3c`, a staging merge commit),
so unfinished mobile schema changes sitting there would have to ride along with
any connector fix, retention fix or security patch. `pathsixdesigns-crm` PR 33
is the precedent: staging had diverged, so two small settings had to be
back-ported to `main` alone. Phase 0 of the mobile plan is roughly a dozen
migrations with backfills, which is a much worse thing to untangle under
pressure.

Staging itself is cordoned and stopped; see section 9 of
`docs/ai-connector-runbook.md`.

## What it is

| Piece | Name |
| --- | --- |
| Fly app | `pathsixsolutions-backend-mobile` |
| Database app | `pathsixsolutions-db-mobile` |
| Region | `iad` |
| Database | `pathsixsolutions_backend_mobile` |
| Administrator login | `pathsixsolutions_backend_mobile` |
| Restricted runtime role | `pathsix_crm_mobile_runtime` |
| Config | `fly.mobile.toml` |
| Hostname | `https://pathsixsolutions-backend-mobile.fly.dev` |

It shares no host, database, login or role with staging, rehearsal or
production. `tests/test_database_targets.py` asserts that, so a later edit
cannot quietly point a mobile operator script at another environment.

Verified after the role cutover: the app connects as
`pathsix_crm_mobile_runtime` with `rolsuper`, `rolcreatedb`, `rolcreaterole`,
`rolreplication` and `rolbypassrls` all false, no CREATE on schema `public`,
SELECT on `clients`, and no access to `alembic_version`. `/api/health` returns
200.

## Deliberate differences from staging

- `min_machines_running = 0` and `auto_stop_machines = "stop"`. Staging keeps one
  machine awake to catch returning MCP calls; nothing here needs that, so the box
  sleeps completely when idle. The second machine Fly adds for high availability
  was destroyed on purpose; one is enough for a development box.
- No `MCP_RESOURCE_URI`, so the AI connector answers 503. This box must never be
  mistaken for one that serves the connector.
- `CRM_RLS_ENABLED = "1"` and the restricted runtime login, the same posture as
  production. This is the point of the environment: mobile migrations add columns
  and tables, and a missing grant has to fail here rather than after a merge.
- No custom domain or certificate. The `.fly.dev` hostname is enough.

## How the schema got here, and why not by migration

`alembic upgrade head` on an empty database fails immediately with
`relation "clients" does not exist`.

**This repository cannot build its database from zero.** The base revision
`add_performance_indexes` carries `down_revision = None  # Update this with your
latest migration`, a leftover scaffold line, and only creates indexes. No
migration anywhere creates `clients`, `leads`, `users` or any other base table.
The schema was originally produced by SQLAlchemy `create_all` before alembic was
adopted, then stamped, and all 20 migrations assume the tables already exist.

The implication reaches past mobile: if both databases were lost, the schema
could not be rebuilt from this repository alone. A snapshot would be required.

So this database was built from a schema-only dump of staging, taken with
`pg_dump --schema-only --no-owner --no-privileges` on the mobile database
machine, with staging woken briefly for the purpose and stopped again after. The
dump carried 23 tables, 25 policies and 36 row-security statements and contained
no rows and no role references, so nothing needed remapping. The
`alembic_version` row was copied separately; the database reports
`mcp_record_updates`, matching the repository's single head, and
`alembic upgrade head` is now correctly a no-op. New Phase 0 migrations will
apply normally on top.

## Data

Synthetic only. No production snapshot is restored here: the box is meant to be
wiped and reseeded while Phase 0 migrations are iterated on, and real customer
records have no business in something treated that way.

The rows came from staging by direct copy, with the owner's approval, because
the seed scripts cannot produce them. `scripts/seed_staging.py` does not
generate a tenant: it refreshes screenshot fixtures for an existing "Staging
Demo Co" tenant and is hardcoded to the staging app, database, hosts and runtime
role. `scripts/seed_staging_testers.py` is hardcoded the same way and also
assumes the tenant and admin user already exist. Neither can bootstrap an empty
database.

The copy took `roles`, `tenants`, `users`, `user_roles`, `accounts`, `clients`,
`contacts`, `leads`, `projects`, `interactions`, `activity_logs`, `files`,
`subscriptions` and `user_preferences` with
`pg_dump --data-only --disable-triggers`. It deliberately left behind the AI
connector tables, OAuth credentials, backups and the chat tables: mobile does
not serve the connector, and its evidence rows belong to staging.
`ai_connections` reads 0 here, confirming that.

What landed, read back through the restricted role with `crm.tenant_id` set the
way a request sets it: tenant 1 "Staging Demo Co", 3 roles, 6 users, 16 clients,
14 accounts, 22 contacts, 35 leads, 16 projects, 26 interactions. Setting a
different tenant id returns 0 clients, so row security is enforcing isolation
here as it does in production. The identity sequences advanced past their
maximum ids (`clients_id_seq` 35, `leads_id_seq` 49, `users_id_seq` 13), so new
inserts will not collide.

The users carry staging's password hashes, so the existing tester logins work.

A real wipe-and-reseed cycle still needs the seed scripts generalized to take a
`--target`, the way `scripts/staging_database_role.py` now does. Until then,
rebuilding this box means repeating the schema dump and this copy.

## Operating it

Deploy:

    flyctl deploy --app pathsixsolutions-backend-mobile --config fly.mobile.toml

Apply new migrations (the chain works from head; it just cannot build from zero).
The app's own `DATABASE_URL` is the restricted runtime role, which has no access
to `alembic_version` and cannot alter tables, so migrations must run as the
administrator. Its host is `.flycast`/`.internal`, reachable only from inside
Fly, so pipe it to the app machine on stdin, the same way the retention script
takes it (`docs/ai-connector-runbook.md`, section 7):

Run it from Git Bash, not PowerShell. Windows PowerShell 5.1 re-encodes a pipe
between two programs with CRLF, the trailing carriage return reaches `read`, and
SQLAlchemy rejects the URL ("Could not parse SQLAlchemy URL"). `tr -d '\r'`
guards against that from any shell:

    powershell -NoProfile -File G:/Projects/tools/pathsix-mobile-admin-url.ps1 | tr -d '\r' | \
      flyctl ssh console --app pathsixsolutions-backend-mobile \
      -C "sh -c 'read -r DATABASE_URL; export DATABASE_URL; cd /app && alembic upgrade head && alembic current'"

A stopped machine must be woken first (any HTTP request to `/api/health`).
flyctl on Windows may print "The handle is invalid." after the remote command
exits; it is harmless once `alembic current` has printed the head.

Run it immediately after a deploy that adds columns the models use: until the
migration commits, the new image's queries against those columns fail with
`undefined_column`, and the health check does not touch the database so it will
not catch this. On this box, with no users, that short gap is accepted. A
production rollout of these migrations needs a no-gap order instead (migrate
from a machine running the new image before it takes traffic).

Corrected November 18, 2026: the earlier one-line form ran as the runtime role
and would fail after the role cutover. Verified the same day by applying
`mobile_stable_ids` (mobile Phase 0, item 1) at commit `d2205a7`: head became
`mobile_stable_ids`, and a read through `pathsix_crm_mobile_runtime` (no RLS
bypass, tenant 1) found all 129 existing rows with a distinct, non-null `uuid`
in each of the six tables, while tenant 2 still saw 0 clients.

The restricted runtime role has already been provisioned and `DATABASE_URL` cut
over to it. If the database is ever rebuilt, provision it again with:

    flyctl ssh console --app pathsixsolutions-backend-mobile \
      -C "python scripts/staging_database_role.py --target mobile"

That reads the administrator `DATABASE_URL` from the machine's environment,
writes a mode-0600 handoff file and prints nothing. Pipe its `runtime_url` into
`flyctl secrets import` and delete the handoff file afterwards. Migrations and
maintenance need the administrator login, which is DPAPI-protected on the
operator workstation and emitted only to a pipe:

    powershell -NoProfile -File G:/Projects/tools/pathsix-mobile-admin-url.ps1 | <command>

It refuses to print to a terminal and refuses any target but the mobile
database, mirroring the staging and production helpers. Never add it as a second
app secret.

Tombstone retention (mobile Phase 0, item 3). Deleted contacts, accounts and
interactions stay as hidden rows for 90 days so offline devices learn about the
deletion; `scripts/prune_tombstones.py` then removes them. Run it monthly, dry
run first, from Git Bash:

    powershell -NoProfile -File G:/Projects/tools/pathsix-mobile-admin-url.ps1 | tr -d '\r' | \
      flyctl ssh console --app pathsixsolutions-backend-mobile -C "python scripts/prune_tombstones.py"

Add `--apply` to delete. It refuses an administrator login that cannot bypass
row security or that points at a different database than the app's own. Since
`mobile_change_feed` (item 4) it also removes `sync_purges` rows (the change
feed's permanent-delete records) older than 90 days; the change feed refuses
cursors older than 89 days, so devices offline longer fully resync.

Test sign-in for the iPhone app (mobile Phase 1). To give a synthetic user a
password the owner knows, pipe it in on stdin; it never appears in an argument,
prompt or log, and the script refuses to run on any other app:

    <password source> | tr -d '' | flyctl ssh console --app pathsixsolutions-backend-mobile       -C "python scripts/set_mobile_test_password.py --email <synthetic user email>"

At least 12 characters. Setting it signs out that user's existing phones. The
script refuses unless the app's `DATABASE_URL` is the mobile database as its
restricted runtime login with row security, and refuses inactive users or
tenants. Database errors print a fixed message only; a failed statement may
still appear in PostgreSQL's own error log with its parameters (the bcrypt
hash, never the password).

Change feed (mobile Phase 0, item 4): `GET /api/sync/changes`, described in
`docs/mobile-sync-feed.md`. The `mobile_change_feed` migration grants the new
`sync_purges` table to the runtime role, which it finds as the single
restricted role holding INSERT on `clients`, so the migration command above
needs no extra argument.

To put the environment to sleep, cordon then stop, as staging does; cordoning is
what prevents the Fly proxy from waking it. To throw it away entirely:

    flyctl apps destroy pathsixsolutions-backend-mobile
    flyctl apps destroy pathsixsolutions-db-mobile

## Cost

One shared 1 GB app machine that sleeps when idle, one 256 MB database machine,
and a 1 GB volume. The volume is the only charge that accrues whether or not
anything is running.
