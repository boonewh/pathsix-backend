# Project and Interaction services — 2026-09-12

ProjectService and InteractionService now own all database operations formerly in
their respective route modules. HTTP adapters validate payloads, supply the trusted
Principal and commit successful mutations. Services flush but never commit. Project
detail is a pure read; the web adapter explicitly records its view. Calendar export
is an authorized, read-only service operation. Project assignment notification is
best effort after commit, with no delivery on commit failure; tests stub email.

TenantService scopes root queries, joins and relationship loads without HTTP hooks.
Shared SQL access predicates also serve search. Project direct assignment overrides
inherited parent access; unassigned projects inherit client/lead permissions, and
standalone projects use their creator. Invalid or deleted parent relationships are
denied. Interactions require exactly one accessible active client/lead/project.
Interaction updates check both current and final parents. Transfer checks both lead
and client, and updates both parent fields in one transaction.

## Deliberate permission corrections

- Project lists reached through a client/lead now respect direct project assignment.
- Interaction lists use the same project-access predicate as mutation/calendar paths,
  rather than granting access solely because someone originally created a project.
- Deleted or malformed-parent records are excluded from active lists. A malformed
  cross-tenant project detail now returns 404 instead of a partial 200 response.
- A directly assigned user can edit non-parent project fields without independently
  owning the linked client/lead; changing parents still requires destination access.
- Project interaction-link access follows project permissions. The legacy creator-only
  non-admin project trash/restore policy remains explicit and unchanged.
- User-email filters explicitly restrict related users to the same tenant, including
  when no HTTP middleware/RLS is present. Admin-only service methods enforce roles.
- Pagination is bounded to positive pages and 1–200 rows. Bulk IDs are positive
  integers; required-name/summary/date nulls are rejected before database writes.

Valid response fields, sorting modes, follow-up completion, calendar fields and
bulk counts are retained. No database migration is required. Backup/import/conversion
and other entity workflows remain separate roadmap work; no AI connection is enabled.

## Validation

Broad local run: 118 passed, 29 PostgreSQL-only skipped. After the final explicit
email filters and additional deleted-parent cases, all 25 focused service tests passed.
Coverage includes standalone/inherited/direct assignment access, foreign records,
admin checks, pure reads, rollback, parent transfer, calendar, view audit and project
notification commit ordering. PostgreSQL adds parent-rule purge conflict coverage.

One additional local test passed for successive project/interaction parent moves
within a single caller-owned transaction. It verifies returned parent details after
each move. This last test was not run on PostgreSQL: the staging machine auto-stopped
and its temporary pytest installation was gone when the final check was attempted.

Staging v25 runs application commit ab4cee2. The focused PostgreSQL run passed
49 tests (101 deselected, 42.33 seconds), covering projects, interactions, search,
parent rules and harness diagnostics. This was not a full PostgreSQL suite. Before
and after that run there were 14 connections and zero test schemas. The filtered
database log stream showed no matching new connection errors during validation;
the earlier intermittent disconnect's root cause remains unresolved.

Live staging login and 33 API checks passed without browser page errors, including
project parent moves, calendar export, completion, transfer, restore, single purge
conflict (409), bulk deletion/purge, and synthetic-record cleanup. An independent
final database check confirmed the original two clients/two leads, zero contacts,
interactions and projects, zero test schemas, parent_link_rules migration head,
fourteen forced RLS tables, CRM_RLS_ENABLED=1 and zero unscoped runtime reads from
clients/leads/users/tenants.

Production and frontend deployments are unchanged. Existing Fly machine sizes,
counts and auto-stop settings are unchanged. No migration or real email was sent.
