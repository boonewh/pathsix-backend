# Tenant membership and index reconciliation

This step adds a database backstop against nonexistent tenant IDs on all eleven
tables with a direct tenant_id: accounts, activity_logs, chat_messages, clients,
contacts, files, interactions, leads, projects, subscriptions and users. It also
ensures a valid full index beginning with tenant_id on each table. User preferences
inherit membership through users; backup metadata and global roles are not assigned
invented tenant IDs by this migration.

The new Alembic revision tenant_membership_indexes merges the three recorded heads
add_project_assigned_to, add_subscriptions_table and add_tenants_table. It inspects
actual constraints/indexes and preserves matching existing objects. Partial or
invalid indexes do not count. A conflicting object name fails rather than silently
masking drift. Historical performance migrations are not replayed: their broader
compound indexes and legacy activity_log table naming need a separate review.

All expected tables and non-null tenant columns must exist, and every existing
tenant reference must be valid before DDL begins. No rows are repaired or removed.
The indexes, validated foreign keys and Alembic head change share one transaction.
Lock waits are bounded to five seconds and statements to sixty seconds. The staging
operator runner also refuses any table above 10,000 rows. This is a small-staging
migration plan; production needs its own preflight and potentially concurrent index
creation. PostgreSQL lock/validation reference:
https://www.postgresql.org/docs/18/sql-altertable.html

## Staging operation

Run scripts/migrate_staging_membership.py on the staging backend with the encrypted
operator URL decrypted locally and supplied through SSH stdin. The runner checks
the exact Fly app, database host/name, administrator username and old/new revision
set. With no arguments it rehearses the DDL inside a rolled-back savepoint. With
--apply it commits the explicit new revision. It does not change runtime credentials
or retain an administrator app secret. It reports only counts and migration heads;
exceptions print only their class, never driver tracebacks.

The old application remains compatible with these additive constraints. Automatic
downgrade is deliberately refused because matching objects may predate the migration;
removing tenant protections requires a separately reviewed migration.

## Boundaries and validation

This does not prevent a row from referencing a different *existing* tenant or a
parent in another tenant. Composite foreign keys and transaction-local RLS remain
next steps; current application authorization remains necessary. No production
deployment or schema operation is authorized.

Local validation: 72 passed, 13 PostgreSQL-only tests skipped. Five new PostgreSQL
tests cover upgrade/history merge, preservation/idempotence, actual FK denial under
the restricted role, invalid-data preflight, atomic rollback on object collision,
partial-index drift and missing-table refusal. All **85 tests passed against staging PostgreSQL in 42.87 seconds** using the
restricted role for HTTP requests and explicit database-denial checks.

Staging release **v14**, application/migration commit
**fd629f59693fd03010f48c2039cd63f9d10f39e2**, passed the rolled-back rehearsal
against actual staging tables, followed by the committed migration. A separate
connection confirmed the new Alembic head, all eleven tenant foreign keys and
indexes, all public foreign keys validated, zero temporary test schemas, and the
unchanged restricted runtime identity. The migration preserved every row.

Live browser login, client/lead reads, search, pipeline reports, the clients page,
and a synthetic client create/read/update/delete/restore/purge all passed with no
page errors. The new test client was removed. Production and frontend deployment
remain unchanged.

Follow-up: staging v16 adds thirty same-tenant composite FKs; see
tenant-relationship-migration.md. RLS and polymorphic relationships remain pending.
