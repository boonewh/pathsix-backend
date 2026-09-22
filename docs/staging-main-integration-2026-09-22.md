# Main into staging — September 22, 2026

Approved scope: staging only. Backend source is staging `7faf6ea` plus main
`2eb62a2`; frontend source is staging `cdab5ed`, its committed handoff updates
through `8aad3a8`, and main `21ed0a5`. Both integrations use merge commits.
No main branch, production deployment, database migration, reseed, dependency
upgrade, or infrastructure resizing is part of this rollout.

## Integration checklist

| Main feature | Combined staging implementation | Checks |
| --- | --- | --- |
| Salesperson Activity report | Existing report API with dates, actor filter and pagination; staging admin authorization | `test_sales_activity`, `test_staging_integration`, browser sales-activity |
| Transactional activity history | Mapper events use bound service principal; bulk soft deletion and purge explicitly record history in the same transaction | rollback, attribution and dedup tests |
| Read recovery and auth diagnostics | Fresh sessions, one bounded disconnected read retry before writes, close lookup before handler, redacted incident details | database-retry, read-recovery, auth-Sentry diagnostics |
| Trash protections | Shared HTTP adapter uses existing principal; PurgeService inherits TenantService; named visible blockers and safe FK fallback; atomic batch | purge-UX suite under restricted PostgreSQL role/RLS, browser Trash |
| Tenant lead defaults | Missing/blank status survives schema validation; service normalizes creation/update; CSV calls the service with row savepoints | lead-options, staging-integration, browser lead-options |
| Request feedback / duplicate form removal | Main UI merged with staging error-body cloning, cancellation, authenticated calendar downloads and reset improvements | frontend typecheck/build and all 33 browser tests |

CI runs the entire combined backend suite with PostgreSQL 18, RLS enabled and a
restricted runtime role. Incoming purge/lead fixtures use the staging harness;
Activity tests use the same runtime factory. Administrative fixture connections
are used only for setup and inspection. Frontend CI runs all browser suites for
staging pushes and PRs, as well as main.

Staging retains current-record authorization for recent activity, service-owned
tenant context, caller-owned transactions, restricted database grants, relationship
constraints, active-user/tenant checks, admin restrictions, JWT validation,
protected calendars and password reset behavior.

## Rollback and deployment order

Deploy backend after passing CI, verify health/source identity/login/protected
reads, then merge frontend staging and verify its Vercel deployment. Build backend
from tracked committed files only using `fly.staging.toml` and an explicit staging
app argument. Preserve two 1 GB app machines and their auto-stop/start settings,
the existing database machine, and `FLY_SCALE_TO_ZERO=1h`.

Backend rollback is the existing RLS-compatible v28 image:
`registry.fly.io/pathsixsolutions-backend-staging:de3f468d68da884e856dd76cd1d024c11c800bc6`
(digest `sha256:d1abe743b3a84f5df41486b8188e16a8e2111d5637d1fd299034fa69fa06b714`).
Never substitute production's pre-RLS image.

Frontend rollback is staging commit `cdab5ed4c92fac04e0e01d0404e3de57e9c1aa0d`,
deployment `https://pathsixdesigns-crm-staging-9o3csfb49-boonewhs-projects.vercel.app`.

## Local work preserved

The original backend, frontend and frontend-staging folders remain on their
original branches. All initially modified and untracked files were hashed before
integration. Their Trash adaptations were compared and incorporated in the clean
worktrees; the original files are retained for review, even where superseded by
the integrated implementation. Backend incident/diagnostic documents and remote
attachments remain untouched. The deferred database-stall investigation remains
outside this integration.

Deployment identities and final checks will be recorded after rollout. Future
main fixes should use this same main-to-staging merge process.
