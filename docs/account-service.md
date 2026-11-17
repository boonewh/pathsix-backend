# Account service — 2026-09-12

All account route database operations now use AccountService: list, detail, create,
update, delete and explicit view audit. The service binds a trusted Principal,
scopes queries and relationship loads to the tenant, and requires an accessible
active client. Updates authorize both the current and destination client. Services
flush without committing; the web adapter owns commit. Detail is a pure read until
the web adapter explicitly records the view. Parent changes expire the loaded
client relationship so subsequent operations in the same transaction return current
client details.

Account schemas reject malformed bodies, null required fields, invalid IDs and
invalid dates before writes. Account number/name lengths match database columns.
Offset-aware dates normalize to UTC before storage and serialization. Existing
blank-date behavior and invalid-status fallback/no-change behavior are preserved.
The frontend form already supplies numeric client IDs. Duplicate route decorators
were removed. No migration or frontend deployment was required.

## Validation and staging

Application commit d07e3ca is deployed on staging v26. Local suite: 138 passed,
29 PostgreSQL-only skipped (52.07 seconds). Focused PostgreSQL run: 20 passed,
90 deselected (26.84 seconds), including account service/HTTP cases and existing
cross-tenant/malformed-relationship regressions. This was not a full PostgreSQL
suite. The four initial local failures were test fixture errors (attempting to
switch identity within one bound session); tests now use one session per identity.

Live staging: 18 HTTP checks passed, including login, list/detail, account creation,
UTC dates, rejected atomic edit, movement between clients, status update, deletion,
404 after deletion, and cleanup. The first smoke runner lacked the requests package
and stopped before network activity; the successful runner used standard urllib.

Independent final verification: original two clients/two leads; zero accounts,
contacts/projects/interactions; zero test schemas; parent_link_rules migration head;
fourteen forced RLS tables; CRM_RLS_ENABLED=1; zero unscoped runtime reads from
clients/leads/accounts/users/tenants. PostgreSQL connections were 15 before and after
the focused run. No new matching connection errors appeared in the concurrent
filtered Fly log stream. The earlier intermittent disconnect cause remains unresolved.

Production, frontend deployments, Fly machine sizes/count and auto-stop settings
are unchanged. No real email was sent. Temporary tests used the existing staging
machine and isolated schemas with registered cleanup.

## Remaining work

Other entity/import/conversion services, explicit background job context and
delegated AI authorization/MCP remain. Account number uniqueness is still globally
defined in the existing schema; tenant-scoped uniqueness needs a separate migration
review before considering the broader isolation inventory complete. No AI access
has been enabled by this milestone.
