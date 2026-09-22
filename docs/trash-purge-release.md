# Trash purge release — 2026-09-22

Release base: backend origin/main 81fa52b and frontend origin/main 4785ef8.
The already-running auth-diagnostics hotfix (original 6c20e17) is preserved.
No staging-only service migration, schema migration, or resource resizing is included.

Single and bulk permanent deletion for clients, leads, and projects share one
admin-only, explicitly tenant-scoped service. It locks eligible parents, counts
visible linked records (including soft-deleted children), and returns named
409 blockers. The entire eligible batch stays intact on conflict. Successful
responses identify actual deleted IDs. Database constraints remain the final
protection; restricted/unseen links receive a category-only safe conflict.
No children are automatically detached or deleted.

The frontend confirms permanent deletion and offers Keep in Trash, Restore and
review, and Refresh Trash. Failed records stay visible and selected. It uses one
bulk request for each record type, suppresses duplicate toasts, and preserves the
production read-activity/recovery behavior. Network ambiguity prompts refresh.

Validation so far: 21 purge/backend regression cases passed locally; 21 frontend
browser checks passed (14 purge plus existing read-recovery and sales-activity).
Frontend typecheck/build passed. Full backend local suite: 45 passed, 2 failed.
Both failures in tests/test_lead_schemas.py reproduce on the unchanged production
baseline (5 passed, 2 failed there); they concern pre-existing schema validation.
CI adds isolated PostgreSQL 18 coverage for purge and production recovery tests.

Deploy backend before merging the frontend to main (Vercel deploys main).
Do not test permanent deletion of customer records as a production smoke check.

Release validation: all 21 purge cases passed on staging PostgreSQL in 11.97s,
using disposable schemas; zero new schemas remained. The production-source
tests ran from /tmp without replacing staging application code or changing its
runtime role/RLS policy. GitHub PostgreSQL regression and frontend build jobs
passed, and both Vercel preview projects completed.

The Fly release Dockerfile pins the previously deployed production dependency
image and copies the reviewed main-based application source. Rollback image:
registry.fly.io/pathsixsolutions-backend@sha256:c1f3e76cbb4a411376827b078aeb84838085fdb4a1cfbd447d25c80a9e0915fb.
Machine sizes/counts and database sleep settings are unchanged by this release.

## Production verification

Backend PR #9 merged to main at e2b91c5489b0bc77a530488a211a9fe3af6a9a9c.
Frontend PR #6 merged to main at 8fcbe4d683879929cfff8d25c538285f39ffd6ba.

The existing Fly machine d894111b636938 is healthy on image
registry.fly.io/pathsixsolutions-backend@sha256:fda1875520195316ea716790484977afdc7d196a41a37be91a13e1783d1f801d.
Deployed purge service, HTTP adapter, and auth diagnostics SHA-256 hashes match
the reviewed source. Health returns 200. A read-only SELECT 1 plus authentication
lookups for a nonexistent user passed for clients, leads, and projects. No real
user was impersonated and no customer record was modified.

Vercel production deployment 6598456577 succeeded for frontend main 8fcbe4d.
https://pathsix-crm.vercel.app/trash serves /assets/index-Zwcc59DK.js, verified to
contain the blocker dialog, restore/review action, and structured purge contract.
Frontend main CI passed. No production customer purge was used as a smoke test.

The initial Depot export failed on a compressed base layer. Retrying the same
source with --compression gzip --depot-scope app --no-cache succeeded before any
production machine update. Normal recurring resources were not resized or added.

Preserve this narrow production change when later promoting the independent
staging security/service branches. The release worktrees remain available under
G:/Projects/pathsix-backend/temp/trash-purge-backend and trash-purge-frontend;
pre-existing working directories and unrelated local investigation edits remain.
