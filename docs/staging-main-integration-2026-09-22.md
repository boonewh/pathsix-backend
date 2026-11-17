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

## Verified rollout

- Backend [PR #12](https://github.com/boonewh/pathsix-backend/pull/12) merged
  normally into staging as `68d7d4b5a8204f86c29721be8dc013c99c0fb0ee`.
  Fly staging **v29** runs that revision on both existing machines. Image digest:
  `sha256:1a93abc877184b76d19564d49b8b4d227742a33e606214428167896788086c1c`.
- Frontend [PR #8](https://github.com/boonewh/pathsixdesigns-crm/pull/8) merged
  normally into staging as `93c9618486fea0dc85ad98330867b9dfe7dd7b45`.
  Vercel deployment:
  `https://pathsixdesigns-crm-staging-12pbj9y9p-boonewhs-projects.vercel.app`.
  Stable URL: `https://pathsixdesigns-crm-staging.vercel.app`.
- Backend [PR CI](https://github.com/boonewh/pathsix-backend/actions/runs/35790681665)
  passed **255 tests with zero skips** against PostgreSQL 18 and restricted-role
  RLS. [Merged staging CI](https://github.com/boonewh/pathsix-backend/actions/runs/35790986390)
  also passed. Frontend typecheck, build and **33 browser regressions** passed
  locally and in [PR CI](https://github.com/boonewh/pathsixdesigns-crm/actions/runs/35790475235)
  and [merged staging CI](https://github.com/boonewh/pathsixdesigns-crm/actions/runs/35791263668).
- Both backend machines passed health checks. Live staging login and protected
  reads (me, clients, leads, projects, Activity) passed before frontend promotion
  and again after cleanup. Deployed runtime is `pathsix_crm_staging_runtime`,
  `rolsuper=false`, `rolbypassrls=false`, `CRM_RLS_ENABLED=1`.
- Schema remains `parent_link_rules` with 14 RLS tables. No migration ran.
  Both existing app machine IDs, 1 GB sizes, auto-stop/start and minimum zero
  settings remain intact. Database machine `830376c7157038` remains 256 MB,
  with `FLY_SCALE_TO_ZERO=1h`. Sleeping still depends on active connections.
- The live frontend bundle `/assets/index-CGpFhWsJ.js` references only
  `https://pathsixsolutions-backend-staging.fly.dev` as its backend host.
  Browser login and dashboard passed. Activity showed correctly attributed
  synthetic changes; Last 7 days filtered 33 events down to the 10 current events.
  Client, lead and project Trash dialogs each displayed the named record and
  its one linked interaction, preserved the record, and offered recovery.
  Lead Restore and review opened the correct detail page. A note-only edit
  saved successfully while preserving `qualified` and `Technology`.
- Cleanup removed only this run's client 15, lead 10, project 3 and interactions
  2/3/4. The parents returned 404 afterward. Transactional audit history remains
  intentionally retained; pre-existing records/history were not purged.
- SHA-256 verification found **zero changes to all 21 originally modified or
  untracked files** (15 backend, 6 frontend). All three original checkout HEADs
  are unchanged. Local evidence and manifests are under
  `G:/Projects/pathsix-backend/temp/staging-integration/`.
- Main refs remain backend `2eb62a2d25a8d5f4aa969f81646be2c8c441a151` and
  frontend `21ed0a5c7b86495ffead7b5e966cc004a592414b`; both are ancestors of
  their integrated staging merges. The production frontend deployment still
  points to `21ed0a5` (September 22, 20:15:33 UTC). No production deploy or
  infrastructure/data mutation was performed.

The final handoff is documentation only; the deployed application source
identities above remain authoritative. Future main fixes should use the same
main-to-staging merge process. Existing frontend bundle-size/Browserslist and
backend deprecation warnings are non-blocking and unchanged in scope. Staging
SMTP delivery and the earlier database-stall investigation remain outside this
rollout; neither was claimed fixed.
