# Client list, assignment and bulk services — September 28, 2026

ClientService now inherits TenantService. All client HTTP database operations run
through it or the existing PurgeService. Services bind a trusted Principal, scope
their own queries without HTTP globals and flush without committing; adapters own
the transaction. Existing client lifecycle methods also use TenantService queries.

## Behavior

- My Clients retains assignment precedence: created records assigned to someone
  else are omitted, including for admins. Assigned Clients contains only the current
  user's assignments. All Clients remains admin-only. Detail/trash retain the shared
  creator-or-assignee/admin policy.
- Personal/admin list response fields and activity filter windows are preserved.
  Pagination accepts positive pages and 1–200 rows, matching other services.
  Invalid pagination returns 400. Unknown sort values still fall back to newest.
- Interaction statistics use one tenant-scoped aggregate with valid client-only
  parents, shared by filtering, sorting and counts. Active + activity sort no longer
  joins interactions twice. Foreign/malformed interactions cannot influence counts,
  dates or membership. Activity sorting puts undated clients last on both SQLite
  and PostgreSQL; ID tie-breaking makes pagination deterministic.
- Display names and admin email filtering use tenant-scoped user queries.
- Assignment requires an admin service principal and an active same-tenant user.
  The schema rejects boolean/string IDs. Notification payloads are returned to the
  web adapter, which commits before best-effort mail delivery. Commit failure sends
  no mail; mail failure does not undo a successful assignment.
- Bulk soft deletion validates positive integer IDs, ignores foreign/missing/already
  deleted records and deduplicates IDs by selecting matching rows. ORM changes keep
  loaded state current and emit exactly one transactional deletion event per changed
  client. Rollback removes both changes and events. Purge protections are unchanged.

## Verification and release

Focused local suite: 13 passed. Tests cover direct calls with and without the
runtime factory, tenant/record boundaries, malformed foreign relationships without
HTTP or RLS, aggregate pagination, response shapes, assignment rollback, mail/commit
failure ordering and transactional bulk history. Local runs use isolated SQLite
fixtures; PostgreSQL CI supplies the restricted runtime role and RLS.

Full local suite: **239 passed, 29 PostgreSQL-only skipped**. Compile and diff
whitespace checks passed. PostgreSQL 18 restricted-role/RLS CI passed **268 tests
with zero skips** in [PR #14](https://github.com/boonewh/pathsix-backend/pull/14).
Merged staging CI also passed.

## Staging rollout

PR #14 merged as `4301ba49627e5cc0bd566ae0a40f967c20e400e9`. Staging **v30**
deployed that source on both existing machines on September 28. Both passed health
checks. The 1 GB VM configuration, HTTP services and sleep settings are unchanged.
Runtime inspection returned `pathsix_crm_staging_runtime`, `rolsuper=false`,
`rolbypassrls=false` and `CRM_RLS_ENABLED=1`.

Live checks passed for synthetic client creation, self-assignment, assigned lists,
active/inactive filters with activity sorting, interaction counts, invalid pages,
bulk soft deletion, named dependency rejection and restore. Cleanup initially used
an unsupported interaction detail GET (405); recovery verified exact IDs and marker
through the interaction list and removed clients 16/17 and interactions 5/6.
No synthetic records remain from this run; transactional history is retained.
All 15 report GET endpoints also passed, with a response baseline saved for the
next report-service rollout. Evidence is under `temp/client-service-rollout/`.

Rollback image: `registry.fly.io/pathsixsolutions-backend-staging:68d7d4b5a8204f86c29721be8dc013c99c0fb0ee`.
No migration, production deployment or main-branch change was performed.

The broader remaining work is tracked in [MCP readiness status](mcp-readiness-status.md).
