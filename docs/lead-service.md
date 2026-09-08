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

This slice does not move lead lists, assignment/email delivery, bulk operations or
permanent purge into services. Those adapters retain their existing protections and
remain on the extraction roadmap. It does not add delegated AI authorization or MCP.
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
