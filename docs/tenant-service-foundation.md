# Tenant-bound service foundation

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
Compile and diff checks passed. Staging result will be recorded after deployment.

## Next steps

This begins Gate 2; it does not complete it. Move the remaining record read/write
paths behind tenant-bound services and consolidate permission predicates. Inventory
all tenant-owned tables before designing same-tenant foreign keys and PostgreSQL
row-level security. Add delegated OAuth access only after those boundaries and the
broader adversarial test matrix are established. Web principal context is not an
AI access token and does not implement OAuth scope or grant enforcement.
