# Trash deletion UX — 2026-09-22

The production bulk-client purge alert exposed an unhandled foreign-key conflict.
This change gives clients (called Accounts in Trash), leads, and projects a shared
permanent-deletion contract and a matching frontend dialog.

## Behavior

- Both single and bulk backend endpoints use `PurgeService`. Admin and tenant
  checks apply before collecting dependency information. Only trashed records
  are eligible. The caller owns commit/rollback.
- A visible dependency produces HTTP 409 with `code: purge_blocked`, an empty
  `deleted_ids`, and `blocked` entries containing the parent ID/name plus linked
  record categories/counts. Counts include soft-deleted children.
- Client dependencies: interactions, contacts, projects, account records, and
  subscriptions. Lead dependencies: interactions, contacts, projects, and
  converted clients. Project dependencies: interactions.
- No automatic unlinking or cascading deletion occurs. The entire eligible
  batch is retained if any parent is blocked. Parent rows are locked in ID order
  during checks/deletion; database constraints remain the final guard.
- Chat remains outside the runtime role's read permissions. FK/check failures
  roll back the request and return a safe conflict; known FK names identify the
  category without parsing SQL, driver details, or hidden record identities.
- Successful responses include exactly the `deleted_ids`. Missing, active, or
  foreign records are not claimed as deleted.

## Frontend

Implemented in `G:/Projects/pathsixdesigns-crm` (`src/components/PurgeDialog.tsx`,
`src/pages/TrashPage.tsx`, and `src/lib/api.ts`). The staging checkout was not
edited separately.

All three record types use one bulk request, including one-item confirmations.
The dialog shows affected names and dependencies, offers Keep in Trash and
Restore and review, and explains that restoration makes the record active again.
Restoration navigates to the detail page only after the restore request succeeds.

The dialog owns error presentation, suppressing duplicate API toasts without
disabling authentication handling or Sentry capture. Unknown/network failures
offer a refresh without falsely claiming rollback or retrying the write. Only
confirmed deleted IDs leave the list; legacy success responses trigger a refresh.
Failed deletions retain their rows and selections. Pending actions cannot be
submitted twice. The modal traps focus and initially focuses the safe option.

## Validation and rollout boundary

Backend regression coverage is in `tests/test_purge_ux.py`: all dependency types,
single/bulk conflicts, caller rollback, HTTP rollback, tenant isolation, input and
admin checks, restore-after-conflict, and FK inventory completeness. Local checks
use isolated SQLite fixtures. PostgreSQL-specific RLS/locking behavior still needs
staging validation; no live database was used for this change.

Frontend browser checks use mocked APIs and disabled Sentry, covering each entity,
blocked and successful batches, legacy responses, HTML/network failures, restoring
for review, pending requests, cancellation, one-item confirmation, and phone layout.
TypeScript, targeted lint, and the production build are also checked.

No migration, production data change, or deployment is included. Release the
backend contract before the frontend to enable detailed blockers. Validate in
staging using synthetic records with and without dependencies before production.
Local source alone does not establish what production is currently running.

## Released

The main-based production port is deployed. See the release receipt in
`temp/trash-purge-backend/docs/trash-purge-release.md` and backend PR #9 / frontend
PR #6. The local staging-branch changes above remain uncommitted and should not
be mistaken for the complete production release branch.
