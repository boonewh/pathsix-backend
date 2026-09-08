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
Staging verification pending. Production remains untouched.
