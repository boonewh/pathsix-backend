# Tenant-bound lead lifecycle

LeadService now owns create, detail, update, soft delete and restore. Every lookup
combines tenant identity with current admin or creator/assignee access. Construction
requires the authenticated immutable Principal and binds the SQLAlchemy session's
transaction-local database context. Validated create data cannot override tenant or
creator identity. Services flush but never commit; adapters own the transaction.

Detail reads do not write audit events. The web adapter explicitly records the view
and commits, preserving the existing HTTP behavior. Nested contacts are selected
with an explicit tenant and exclusive lead-parent predicate. Update preserves phone
normalization and the existing conversion timestamp on a transition to won. Explicit
null names and non-object update bodies return 400 rather than a database/server error.

The follow-up slice moves personal/admin/assigned/trash lists, bulk soft deletion,
bulk purge and single permanent purge into the service. Admin authorization is
enforced inside the service as well as the HTTP adapter. Assignment now also uses
the service; the web adapter delivers its notification after commit. These slices
do not add delegated AI authorization or MCP.
No schema migration is needed; parent-link constraints and RLS remain active.

## Validation

Local suite: 79 passed, 29 PostgreSQL-only skipped, one test assertion corrected to
use the existing contact response field; that focused test then passed. Tests prove
cross-tenant denial without HTTP, same-tenant ownership, nested-contact filtering,
pure reads, rollback, conversion status behavior and the HTTP lifecycle/view audit.

Staging v21 / 1b32d82 verified on 2026-09-08: all 109 PostgreSQL tests passed in
75.52 seconds, with RLS and the restricted runtime role. Live browser login,
thirteen protected reads and the lead/contact lifecycle passed without page errors,
including conversion date, null-name rejection, deleted-detail denial, parent purge
conflict, restore and cleanup.

Independent verification confirmed Alembic head parent_link_rules, fourteen forced
RLS tables, zero test schemas and zero unscoped runtime clients/leads/users/tenants.
The original two clients/two leads remain, with contacts/interactions/projects empty.
Production and frontend deployments remain unchanged. No Fly machines were added
or resized, and the existing auto-stop configuration is unchanged.

## List and deletion follow-up

Personal lists preserve assigned-to-me plus unassigned-created-by-me behavior even
for admins; the separate admin list sees active leads in the current tenant. Trash
preserves creator/assignee access for ordinary users. Response fields and user-email
filtering are retained. Display names are fetched in one tenant-scoped batch, so a
legacy malformed user relationship cannot expose another company's email.

Pagination requires a positive page and 1–200 rows per page. Bulk IDs must be a
nonempty list of positive integers; booleans/strings are rejected. Mixed-tenant IDs
retain the existing behavior of affecting only eligible current-tenant rows. The
adapter commits once; parent conflicts still roll back the entire bulk purge.

Local verification: 87 passed, 29 PostgreSQL-only skipped; two focused list checks
also passed after consolidating response serialization. Staging v22 / 8f1aa06:
116 PostgreSQL tests passed in 138.48 seconds with RLS and the restricted role.
Live login, all four lead lists, protected CRM reads, pagination rejection and a
two-lead bulk delete/purge workflow passed without browser errors. A contact blocked
the first bulk purge; both leads remained in trash, then removal of the contact
allowed successful cleanup. Independent checks confirmed zero test schemas, original
two clients/two leads, zero contacts/interactions/projects, fourteen forced RLS
tables and zero unscoped runtime reads. No schema migration, production/frontend
deployment or Fly resource size/count changes were made.

## Assignment follow-up

LeadService.assign requires admin access, validated positive integer user IDs, an
active lead in the current tenant and an active assignee in that tenant. It flushes
without committing and returns notification fields. The web adapter commits and
closes the database session before attempting email delivery. A failed commit cannot
send a notification, and database exception details are no longer returned to the
client. Email remains best effort: delivery failure does not undo assignment, and
there is no durable outbox or automatic delivery retry yet. Password-reset/Resend
configuration is unchanged.

All operations in app/routes/leads.py now call the shared lead service for database
work. Separate imports, conversion and other entity routes still require their own
service review. This is not a claim that every workflow involving a lead is migrated.

Local verification: 90 passed, 29 PostgreSQL-only skipped. New tests cover service
authorization, invalid/inactive/foreign assignees, deleted leads, rollback, commit
ordering, safe database error responses and mail failure. Notification calls are
stubbed; no test email is sent.

Staging v23 / c23b53b: all 119 cases passed across the initial run and targeted
reruns, not in one uninterrupted suite. The first run had 88 passes and 31 setup
errors; the three assignment tests passed separately, then 27 of 28 remaining
cases passed, and the last case passed on rerun. Captured diagnostics show the
database closed a connection during schema reflection/setup. The underlying cause
is unconfirmed; Fly database health checks subsequently passed. Keep this staging
connection interruption on the reliability follow-up list; do not erase it as a
clean full-suite result. One orphan disposable schema was inspected and removed.

Live browser login, lead lists/detail, other protected reads and invalid assignment
rejection passed without page errors. Successful assignment/mail sequencing was
validated through stubbed PostgreSQL integration tests, not real email delivery.
Independent checks confirmed zero test schemas, original two clients/two leads,
empty contacts/interactions/projects, fourteen forced RLS tables and zero unscoped
runtime reads. Production, frontend deployments and Fly resource configuration are
unchanged. No production rollout is authorized.
