# MCP readiness status — September 29, 2026

This reconciles the historical September 6 frontend roadmap against the integrated
backend. The goal remains a remote MCP adapter over the same tenant-bound services
as the CRM, with delegated user consent and read-only tools before writes.

## Baseline and release status

Work resumed from staging `aa1d830`, which merges the September 22 handoff.
The current verified live backend is **v37 / `3770a2d`** after the client, report, storage,
import, user, preference, identity/platform, isolation-proof and AI consent increments. PostgreSQL/RLS CI
passes **487 tests with zero skips**; both
staging machines pass health checks and runtime inspection confirms restricted
credentials with RLS enabled. Frontend `93c9618` was not changed or redeployed.
The earlier 33 browser regressions belong to the September 22 rollout, not new
browser testing for these backend-only increments. See
[the earlier integration handoff](staging-main-integration-2026-09-22.md).

The client-service increment merged through PR #14 and first deployed as v30 /
`4301ba4`. See [client operations](client-service-operations.md). The report-service
increment merged through PR #15 and deployed as v31 / `30c6ab2`; see
[report service](report-service.md) for verification, rollout and rollback details.
Storage operations now use StorageService through PR #17, deployed as v32 /
`2149bb6`. All 25 focused storage checks passed against deployed code in disposable
PostgreSQL schemas, with public users/files unchanged. See [storage service](storage-service.md).
Import orchestration now uses ImportService through PR #18, deployed as v33 /
`dfd8230`. All 25 focused import checks passed against deployed code in disposable
PostgreSQL schemas, with public data unchanged. See [import service](import-service.md).
User administration and personal preferences now use UserService and PreferenceService
through PR #19, deployed as v34 / `448a6c0`. All 40 focused user/preference and
password-reset checks passed against deployed code in disposable PostgreSQL schemas.
Public users, roles and preferences were unchanged. See
[user and preference services](user-preference-services.md).
Current-user configuration/password operations now use IdentityService, and platform
backup/restore/cleanup jobs have an explicit fail-closed worker boundary through PR #20.
Staging v35 / `6c8ce18` passed all 36 focused identity/platform checks, including
concurrent password changes. Public credentials and identity/configuration stayed
unchanged, and platform jobs remain disabled. See
[identity and platform boundaries](identity-platform-boundaries.md).
Session identity is now fixed across cached access, transaction lifecycle and request
reuse. A complete model/table inventory and deliberate predicate/policy failures
close the named current-web proof gaps through PR #21. Staging v36 / `898cc7a`
passed all 50 focused tests, the 14-table/20-policy live attestation, and unchanged
full-row digests across all 17 public application tables. See
[isolation proof](isolation-proof.md).
Owner-bound AI consent records and their management APIs now support explicit read
permissions, expiry, terminal revocation and current-state checks through PR #22.
Staging v37 / `3770a2d` passed 92 focused consent/migration/isolation checks, with
all 19 public application tables unchanged by verification. The two new tables are
empty; no AI client, token endpoint or MCP tool is enabled. See
[AI connection consent](ai-connection-consent.md). The handoff documentation does
not change v37's verified application source.

## Reconciled gates

| Gate | Status | Evidence and remaining work |
| --- | --- | --- |
| 0: operational baseline | Partial | Integrated staging source and CI are reproducible. Staging DB sleep is documented as one hour. Original dirty checkouts are preserved separately. Full resource/cost inventory, restore drill and earlier staging DB-stall investigation remain open. |
| 1: immediate REST security | Completed documented milestone | Current server-side roles and active user/tenant checks, parent authorization, admin reports/import, protected calendars, disabled tenant backup API, JWT validation and reset protections. See reliability-security-2026-09-06.md. |
| 2: structural isolation | Current web foundation implemented; delegated identity remains | Trusted fixed-identity web sessions, tenant services, restricted DB role, tenant FKs/indexes, 30 earlier same-tenant relationship FKs plus the new consent-owner FK, parent rules and forced RLS on 15 tables. Model inventory classifies all 19 tables. Authenticated delegated Principal remains Gate 4 work; model caveats below remain open. |
| 3: adversarial isolation proof | Current web proof gaps addressed; delegated/MCP proof remains | Tests cover service, HTTP, file/object and platform boundaries plus session/cache reuse, all protected tables, aggregate secrecy and deliberate missing/weakened predicates/policies. Read-only policy attestation detects drift. See isolation-proof.md; this is not a certification of future grants/scopes or operational durability. |
| 4: delegated AI authorization | Consent/grant foundation implemented; OAuth flow remains | Owner-bound persisted grants, scoped approval/expiry/revocation APIs and current-state permission checks are verified. Authorization-server integration, discovery, browser PKCE consent, resource-bound tokens, refresh rotation/token revocation and connected-AI UI remain. Grant IDs and web Principal are not delegated credentials. |
| 5: read-only MCP pilot | Not implemented | No MCP endpoint or tools. Tool scope checks, bounded/minimized results, durable MCP audit events and MCP isolation tests remain. |
| 6: write tools | Not implemented | Separate write grants, confirmation, idempotency, optimistic concurrency and replay/partial-failure tests remain. |
| 7: production MCP rollout | Not started | Threat model, external review, distributed limits, monitoring/runbooks and gradual opt-in pilot remain. |

## Service boundary inventory

- Search, leads, contacts, projects, interactions, accounts, subscriptions, recent
  activity and purge have tenant-bound service implementations.
- Client lifecycle was already extracted. This increment adds all client lists,
  activity filters/statistics, assignment and bulk soft deletion; the client HTTP
  module now has no direct ORM queries. Permanent deletion uses the existing shared
  PurgeService adapter.
- ImportService now owns bounded CSV/XLSX parsing, mappings, tenant-bound active
  assignee lookup and row orchestration through LeadService. Routes adapt requests,
  commit and send the prepared notification after commit. Direct service calls
  enforce admin authorization; row failures preserve batch rollback semantics.
- All report reads now use an admin-only ReportService, including
  the legacy summary alias and Sales Activity adapter. StorageService owns file
  metadata/object access with current-role checks, confined paths and ordinary-failure
  compensation. User administration now uses an admin-only UserService. PreferenceService
  scopes personal preferences by both user and tenant, including outside HTTP/RLS.
  IdentityService owns current-user/configuration reads and authenticated password
  changes. Login and signed-reset lookups remain authentication-boundary operations.
  Existing background jobs are global backup/restore/retention: their entry points
  now require an explicit operation allowlist and privileged PostgreSQL connection
  without tenant identity, and reject HTTP contexts. They remain outside tenant/MCP
  APIs and disabled on the web deployment. Operator/queue security, restore approval
  and actual backup/restore reliability still require review.
- Review remaining model issues separately, including global account-number
  uniqueness and polymorphic activity references. Database RLS protects tenant
  boundaries; record permissions within a tenant still require service predicates.

## Next increments

1. Client services are verified in PostgreSQL CI and deployed to staging.
2. ReportService is verified in PostgreSQL CI and staging. Report reads now have a
   reusable admin boundary; no MCP scope enforcement is implied.
3. StorageService is verified in PostgreSQL CI and deployed to staging. Durable
   storage/reconciliation and bounded file reads remain prerequisites for a file MCP
   pilot; staging currently has ephemeral local storage on two machines.
4. ImportService is verified in PostgreSQL CI and staging. Import idempotency and
   durable notification delivery remain prerequisites for MCP import writes.
5. UserService and PreferenceService are verified in PostgreSQL CI and staging.
   Current web identity/role checks are preserved; no delegated administrative grants
   or MCP account-management tools are enabled.
6. IdentityService and the fail-closed platform-job boundary are verified in PostgreSQL
   CI and staging. No real platform job was run or enabled. Keep the operational DB-stall issue
   visible; do not infer it is fixed from a successful CI run.
7. The current-web Gate 2/3 proof gaps are addressed by fixed-identity sessions,
   a complete model/table inventory, missing/weakened protection tests and policy
   attestation. See [isolation proof](isolation-proof.md) for the exact evidence
   and remaining limits.
8. Consent/grant storage and owner management are verified in PostgreSQL CI and staging.
   Negative scope, expiry, revocation, inactive-identity and concurrent-limit tests
   pass. Next: the browser authorization-code/PKCE flow, token issuance and refresh/
   revocation integration, then the connected-AI UI and bounded read-only MCP pilot.
   No external AI access is enabled by the consent foundation alone.
