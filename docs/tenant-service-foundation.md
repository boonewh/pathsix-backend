# Tenant-bound service foundation

Latest: client lifecycle migration and live schema audit completed in staging v10
(`64dfe15`, 68 passing tests). See [the client-service report](client-service-and-schema-audit.md).
The search milestone below is retained as history.

## First migrated path: global search

`GET /api/search` now delegates to `SearchService`. Authentication constructs a
frozen `Principal` snapshot from the current database user's ID, tenant and roles.
The service requires that context and applies explicit tenant/access predicates
without depending on Quart request globals or the request ORM hook. Every adapter
must authenticate and create fresh context for each operation. Never accept a
principal, role, tenant ID or connection type from client JSON or cache a principal
across requests. The current connection type is web; OAuth grants/scopes and
connection IDs are not implemented yet.

The existing search response shape is preserved. Assigned clients are included;
accounts inherit parent access; projects honor direct assignment before inherited
parent access. Project results link to their own details so an assigned project
does not lead users to an inaccessible parent page. Deleted parents and malformed
cross-tenant parent links are excluded even for tenant admins. Admin user search
remains within its tenant. Query text is bounded at 200 characters, wildcard
characters are literal, results have deterministic ID ordering, and each resource
type returns at most 10 rows by default (20 is the service's absolute limit).

The backend feature branch remains `codex/crm-reliability-security`. No schema
migration, frontend source change, MCP exposure or production deployment is included.

## Verification

Local: 61 tests passed, including direct service tests outside HTTP context, two
companies, ordinary/admin users, creator/assigned/inherited project permissions,
deleted or cross-company parents, wildcard literals, bounds and role revocation.
Compile and diff checks passed. Staging release **v9** deployed commit
`c88ce352168967ddc83c4424ce52ffe97849744b`; all 61 tests passed against PostgreSQL
in 24.34s. Cleanup verification found zero temporary test schemas. Live staging browser login/search/dashboard checks passed without page
errors; overlong searches returned 400 and ordinary searches returned 200.
Frontend source/deployment was not changed. Production was not changed.

## Next steps

This begins Gate 2; it does not complete it. Move the remaining record read/write
paths behind tenant-bound services and consolidate permission predicates. Inventory
all tenant-owned tables before designing same-tenant foreign keys and PostgreSQL
row-level security. Add delegated OAuth access only after those boundaries and the
broader adversarial test matrix are established. Web principal context is not an
AI access token and does not implement OAuth scope or grant enforcement.


## Model inventory for the next database pass

This is a code inventory, not a statement about the live database schema.

| Ownership | Tables | Notes |
| --- | --- | --- |
| Direct tenant | users | Declares tenant foreign key and index |
| Direct tenant | clients, leads, projects, interactions, accounts | Tenant IDs lack model-level tenant foreign keys; several indexes exist in performance migrations |
| Direct tenant | contacts, activity_logs, chat_messages, files, subscriptions | Model tenant indexes, no tenant foreign keys |
| Through user | user_preferences, user_roles | Require user ownership; no tenant column |
| Shared definitions | roles | Role names are global; membership is per user |
| Company registry | tenants | Root of tenant identity |
| Platform only | backups, backup_restores | Whole-database operations; HTTP blueprint remains unregistered |

Before adding constraints, compare actual PostgreSQL constraints/indexes to migrations,
audit orphan and cross-tenant parent/user IDs using counts only, and design reversible
staging migrations. Do not infer that an index is missing solely from ORM metadata.
Inventory storage paths/import workflows and global backup workers alongside tables.
