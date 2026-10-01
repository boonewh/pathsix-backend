# MCP readiness status — September 30, 2026

This reconciles the historical September 6 frontend roadmap against the integrated
backend. The goal remains a remote MCP adapter over the same tenant-bound services
as the CRM, with delegated user consent and read-only tools before writes.

## Baseline and release status

Work resumed from staging `aa1d830`, which merges the September 22 handoff.
The current verified live backend is **v40 / `bf6af89`** after the client, report, storage,
import, user, preference, identity/platform, isolation-proof, AI consent, OAuth browser and read-only MCP increments. PostgreSQL/RLS CI
passes **594 tests with zero skips**; both
staging machines pass health checks and runtime inspection confirms restricted
credentials with RLS enabled. Frontend `93c9618` was not changed or redeployed.
The earlier 33 browser regressions belong to the September 22 rollout. The new
backend-served OAuth screens have separate synthetic mobile/desktop browser evidence. See
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
all 19 public application tables unchanged by verification. The two new tables were
empty at that release; no AI client or MCP tool was enabled. See
[AI connection consent](ai-connection-consent.md). The handoff documentation does
not change v37's verified application source. The subsequent OAuth browser flow adds
authorization-server discovery, explicit mobile consent, resource-bound opaque tokens,
refresh rotation/replay revocation and a backend-served connection management page.
Staging v38 passed 136 focused tests using disposable schemas over the private
database endpoint, with all 20 public application tables unchanged. An initial
Flycast connection drop and database resource-pressure signal remain recorded as
an open operational issue. See [OAuth browser flow](oauth-browser-flow.md).
The subsequent [read-only MCP pilot](mcp-readonly-pilot.md) adds client summaries,
protected-resource metadata and durable invocation auditing. Staging v39 passed
190 deployed checks with all 21 public application tables unchanged and both
machines healthy. ChatGPT was subsequently registered as `pathsix-chatgpt-staging`.
Staging v40 accepts ChatGPT's optional language hint without weakening OAuth
validation. The September 30 phone test completed sign-in, approval, code exchange
and refresh rotation, then called both `list_clients` and `get_client` successfully.
Backend audit records and actual ChatGPT tool history corroborate the two staging
client summaries. The initial list request exceeded the maximum page size and was
correctly rejected; ChatGPT retried with valid arguments. Production is not enabled.

## Reconciled gates

| Gate | Status | Evidence and remaining work |
| --- | --- | --- |
| 0: operational baseline | Partial | Integrated staging source and CI are reproducible. Staging DB sleep is documented as one hour. Original dirty checkouts are preserved separately. Full resource/cost inventory, restore drill and earlier staging DB-stall investigation remain open. |
| 1: immediate REST security | Completed documented milestone | Current server-side roles and active user/tenant checks, parent authorization, admin reports/import, protected calendars, disabled tenant backup API, JWT validation and reset protections. See reliability-security-2026-09-06.md. |
| 2: structural isolation | Current web and delegated-identity foundations implemented | Trusted fixed-identity web sessions, tenant services, restricted DB role, tenant FKs/indexes, 30 earlier same-tenant relationship FKs plus grant/credential/audit owner FKs, parent rules and forced RLS on 17 tables. Model inventory classifies all 21 tables. The narrow MCP adapter validates raw credentials and scopes before invoking ClientService; model caveats below remain open. |
| 3: adversarial isolation proof | Current web and limited client MCP proof implemented | Tests cover service, HTTP, file/object and platform boundaries plus session/cache reuse, all protected tables, aggregate secrecy and deliberate missing/weakened predicates/policies. Read-only policy attestation detects drift. The two client MCP tools add protocol, scope, revocation, ownership and audit-failure proofs. See isolation-proof.md and mcp-readonly-pilot.md; broader tools and operational durability remain separate gates. |
| 4: delegated AI authorization | ChatGPT staging connection verified | Owner grants, browser S256 consent, discovery, hashed short resource-bound tokens, refresh rotation/replay revocation, current-state validation and mobile connection management are implemented. Real ChatGPT sign-in and exchange passed; automated client registration remains separate. Grant IDs and web JWTs cannot authenticate delegated access. |
| 5: read-only MCP pilot | Real ChatGPT phone reads verified | Official SDK stateless transport; list_clients/get_client with clients:read, existing record permissions, bounded summaries, append-only owner audits and durable call limits. Both tools were called successfully from the user's phone. |
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
   pass. The OAuth browser flow and backend connection-management screen are now
   implemented for operator-registered HTTPS clients. ChatGPT staging enrollment
   and the real phone test are now complete.
9. The read-only client MCP adapter and its adversarial proofs are implemented.
   The real ChatGPT staging consent and read trial is complete. Existing automated
   revocation tests remain the evidence for revocation, not a claim that the user's
   working ChatGPT grant was revoked.
10. Onboarding recovery now distinguishes expired approval requests, failed sign-in
    and unavailable service; both browser pages identify staging accounts and allow
    account switching. See [OAuth browser flow](oauth-browser-flow.md). Deployment
    and verification of this increment are recorded with its release.
11. The intended customer release remains the two read-only summary tools. Next:
    production operational readiness (including shared abuse limits and monitoring),
    production OAuth client configuration, and public submission materials. OpenAI
    review/publication is the documented distribution route for a simple installation
    by independent customers; private testing can continue without publication.
    Dynamic registration/CIMD is a separate interoperability improvement, not a
    prerequisite for every customer to use a single preconfigured published client.
