# Client service and staging schema audit

## Service migration

Client create, detail, update, soft delete and restore now use `ClientService` with
an authenticated principal. Client/lead SQL ownership predicates are shared with
search. Assignment, lists and bulk/purge paths remain in routes and are not claimed
as migrated. Ordinary users retain creator-or-assigned access; admins stay within
their tenant. Identity fields are always server supplied.

Detail reads explicitly scope contacts and source leads without an HTTP context.
Deleted source leads and contacts with invalid dual parents are not serialized.
Reads are pure; the website separately records its existing viewed activity.
Service mutations flush but never commit, allowing adapters to own the transaction.
Routes commit only a successful operation and close/roll back on failure. A null
client name now returns 400 rather than a database integrity error.

## Live staging audit (read-only, historical)

The superuser finding was resolved in staging v12/v13. Direct tenant foreign keys
and full tenant indexes were reconciled in v14; see tenant-membership-migration.md.
Composite foreign keys and RLS below remain outstanding.

`audit_staging_schema.py` verified the staging app and database hostname before
opening a read-only transaction with a 15-second statement timeout. It returned
schema metadata and counts only, with no CRM content or credentials.

- Staging records all three current Alembic heads: `add_project_assigned_to`,
  `add_subscriptions_table`, `add_tenants_table`. The missing indexes therefore
  represent schema/history drift; an apparent current version is not proof that
  every historical operation was applied.
- Every checked tenant membership/foreign-key relationship had zero invalid rows.
  This is synthetic staging data, not evidence about production rows.
- Only `users` has a tenant foreign key. No composite foreign keys were present.
- `accounts`, `clients`, `leads`, `projects` and `interactions` have no tenant index
  in live staging, despite several such indexes appearing in migration source.
- All inspected public tables have row-level security disabled.
- The staging application's database role is a superuser. PostgreSQL superusers
  bypass row security, even though this role's explicit BYPASSRLS flag is false.
  Reference: https://www.postgresql.org/docs/current/ddl-rowsecurity.html

No schema, database credential, production resource or frontend source was changed.

## Migration sequence informed by the audit

1. Reconcile live Alembic revision and index drift; do not blindly replay a historical
   migration that may contain incompatible columns or already-existing objects.
2. Add tenant indexes and tenant foreign keys with a preflight and bounded lock time.
3. Add unique parent `(tenant_id, id)` keys and composite foreign keys for parent
   and user references, plus parent-cardinality constraints. Validate existing data.
4. Give the application a separate non-superuser role, with only necessary table
   and sequence grants; keep schema ownership/migrations and backup operations separate.
5. Design transaction-local tenant context and RLS policies, including authenticated
   user lookup/login/bootstrap and background tasks. Test with the actual restricted
   role; superuser tests cannot demonstrate RLS isolation.
6. Prove pool reuse, missing tenant context, cross-tenant joins and writes fail closed
   in disposable schemas before applying policies to staging's public schema.

Production still requires its own read-only schema/data preflight before promotion.
No production deployment is authorized.

## Validation

Local suite: 68 tests passed, including standalone service denial tests, transaction
rollback, assigned-user lifecycle, scoped nested records and the HTTP lifecycle.
Compile and diff checks passed. Staging release **v10**, application/test commit
`64dfe159b046f018bde020b85a61990100d2d54e`, passed all **68 tests** against PostgreSQL
in 25.85 seconds. Live frontend login, client list/detail API contract and client
page navigation passed without page errors. Cleanup confirmed zero test schemas
and unchanged counts of two synthetic clients and two leads. Production unchanged.
