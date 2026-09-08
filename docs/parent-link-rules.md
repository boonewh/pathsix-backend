# Parent-link rules

Migration parent_link_rules follows tenant_row_security. It enforces the current
web API contract: contacts have exactly one client/lead parent, interactions have
exactly one client/lead/project parent, and projects have at most one client/lead
parent (standalone projects remain valid).

Existing data is checked before DDL; invalid combinations are reported by count
and left untouched for operator review. Three named CHECK constraints are added
and validated in one transaction with five-second lock and sixty-second statement
timeouts. The guarded staging runner still checks the exact app, host, database,
operator username, revision history and bounded staging table sizes. A rehearsal
rolls back all DDL; --apply commits only the requested revision.

Use scripts/migrate_staging_membership.py --revision parent_link_rules with the
operator connection supplied via SSH stdin, followed by --apply after verification.
RLS and CRM_RLS_ENABLED=1 remain in place. These constraints are migration-managed;
model create_all alone does not install them.

## Deletion behavior

Permanent deletion must not silently detach required children. A foreign-key or
parent-rule conflict on a deletion/purge returns HTTP 409 with a message asking
the user to remove or reassign related records. The whole operation rolls back,
including bulk purges. A soft-deleted parent can be restored before managing its
children; no automatic deletion or reassignment of child data occurs.

## Validation

Tests cover every parent-field combination under the restricted PostgreSQL role,
atomic parent transfer, invalid-data preflight, DDL rollback, and single/bulk purge
conflicts with child preservation and successful deletion after resolution.
Verified 2026-09-08 on staging v20, application commit 322f057, Alembic head
parent_link_rules. Local suite: 74 passed, 28 PostgreSQL-only checks skipped.
The PostgreSQL suite passed all 102 tests before the final lead relationship fix;
the focused final client/lead purge run passed all three selected tests.

Client and lead chat backrefs use passive deletion so purging does not query the
unused chat table under the restricted login. Database foreign keys still protect
references; no database privileges were broadened.

The rolled-back rehearsal and applied migration both passed. Independent inspection
confirmed all three CHECK constraints are validated, all fourteen RLS tables remain
enabled/forced, and unscoped runtime reads return zero clients/leads/users/tenants.
Live browser login, thirteen protected reads, and client/contact and lead/contact
conflict/restore/cleanup workflows passed without page errors. The original two
clients and two leads remain; contacts/interactions/projects and temporary test
schemas are empty. Production and frontend deployments remain untouched. No Fly
machines were added or resized; existing auto-stop settings are unchanged.
