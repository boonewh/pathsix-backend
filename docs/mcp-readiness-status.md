# PathSix ChatGPT MVP plan and readiness — October 6, 2026

This reconciles the historical September 6 frontend roadmap against the integrated
backend. The goal remains a remote MCP adapter over the same tenant-bound services
as the CRM, with delegated user consent. On October 6, the user approved a first
public release covering everyday read and write workflows before submission for
OpenAI review. The read-only staging pilot is the proven foundation, not the
intended complete customer release. The September 30 deployment and test evidence
below remains historical; this scope update does not establish a newer deployment.

## MVP scope lock and agent working rules

**Status: scope locked by the user on October 6, 2026; build incomplete.**
This MVP is the only planned product build until its completion criteria are met.
The approved workflow table, implementation steps and completion checklist below
define the work. Older roadmaps and historical gaps in this document do not add
tasks to the MVP. This lock supersedes the earlier read-only release target.

- Work toward completing this MVP. Make routine implementation choices and the
  supporting code, schema, test and UI changes necessary for its listed workflows.
  Prefer the smallest adequate solution using existing services. Do not add
  speculative scaling, general refactors, redesigns or extra tools as improvements
  to do along the way.
- The agent is allowed to investigate and fix discovered bugs or issues without
  asking for separate scope approval. Keep repairs bounded to restoring intended
  behavior, security, data integrity or reliability; document the issue, repair
  and relevant verification. A new capability or broad architectural improvement
  is not a bug fix merely because it would be useful.
- If the agent believes work outside this MVP should be done, state that it is
  outside scope, explain why it matters and its effect on the MVP, and ask the user
  before implementing it. Wait for an explicit answer for that work; continue
  independent MVP tasks. If it is a prerequisite, identify the blocked acceptance
  criterion rather than silently expanding the plan.
- Other issues and ideas may be recorded under **Future consideration** at the
  bottom. Recording an item does not authorize implementation or make it an MVP
  requirement. Do not work through that list during this build.
- Only the user can approve a scope change. Record their decision and update the
  affected scope and checklist before treating an addition as required. Do not
  reopen completed milestones merely to improve or scale them without a defect
  or an approved change.

These are scope rules, not a change to existing environment permissions or approval
requirements for operational actions. Once the MVP is complete, report the evidence
and remaining external release steps; do not automatically begin deferred work.

## Baseline and release status

The latest verified deployment is **staging v45 / `5bf98b4`**, October 7, 2026
(America/Chicago), with an OAuth challenge for missing tool scopes and 675 passing
PostgreSQL CI tests. Ten focused deployed tests passed without skips, with unchanged
public data/sequences, clean test schemas and unchanged database protections. The
verification machine and public health/discovery passed; both machines have the
same v45 image, but the second machine was asleep during verification. The lead implementation
in v43 passed 149 isolated staging checks with unchanged public data/sequences
and clean test schemas. The
staging client permits fresh lead consent; existing grants remain unchanged.
After the user explicitly approved adjustment of the diagnostic OAuth request,
fresh three-scope consent and real client/lead reads using that grant succeeded.
A fresh ChatGPT conversation discovered `list_leads`; its successful call is
corroborated by the staging audit. Ordinary reconnect scope behavior and the real
ChatGPT confirmation/recovery trial remain open. Live lead reading and cancellation
passed, but no live test lead has been created. The new challenge still needs real
ChatGPT verification; step 1 is not complete. See
[lead-creation release evidence and limits](mcp-lead-creation.md).

Work resumed from staging `aa1d830`, which merges the September 22 handoff.
The earlier historical deployment was **v40 / `bf6af89`** after the client, report, storage,
import, user, preference, identity/platform, isolation-proof, AI consent, OAuth browser and read-only MCP increments. PostgreSQL/RLS CI
passed **594 tests with zero skips**; both
staging machines passed health checks and runtime inspection confirmed restricted
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
| 6: write tools | Lead creation deployed; live cancellation passed, confirmation/recovery and normal permission recovery pending | [Lead creation](mcp-lead-creation.md) adds separate consent, browser confirmation, atomic receipts and replay/partial-failure tests; 149 implementation and 10 subsequent scope-recovery deployed checks passed. Contact/project creation, updates, assignments/status changes, notes/interactions and optimistic concurrency remain required before submission. |
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

## Historical increments and release handoff

Items 1–10 summarize earlier work and its limits; they are not an additional work
queue. Item 11 hands off to the locked MVP below.

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
11. Complete the approved everyday read and write workflows below before public
    submission. The bounded operational checks, production OAuth configuration,
    and submission materials in the MVP checklist remain release requirements. Private staging testing
    can continue while these increments are built. OpenAI approval and publication
    remain external milestones, not outcomes established by a successful connection.

## Locked MVP capabilities

The October 6 decision is to submit a useful CRM workflow integration, including
writes, rather than submit only the two client-summary tools. Customers should be
able to connect, sign in, approve specific access, and use their existing company
and record permissions through ChatGPT.

| Customer workflow | Existing foundation | MCP work required |
| --- | --- | --- |
| Find and review clients, leads, contacts and projects, with relevant interactions | SearchService and record services; two client-summary tools are verified | Bounded search/detail contracts and explicit read permissions for the additional records and fields |
| Create leads, contacts and projects | LeadService.create, ContactService.create, ProjectService.create | Scoped creation tools, parent selection, validated inputs, confirmation and durable duplicate-request protection |
| Update client, lead, contact and project details | Existing record update services | Explicit field allowlists, current-record checks, conflict detection and confirmed updates |
| Change assignments and statuses where the CRM supports them | Existing assignment operations and schema validation | Authorized assignee selection, current-role checks, existing status rules and safe handling of notification side effects |
| Record notes and interactions | Existing record notes fields and InteractionService | Clear append-versus-replace semantics for notes and confirmed, repeat-safe interaction creation |

Use these boundaries when implementing the table:

- Reads cover bounded lists/search and selected record details for the listed
  entities. Contact selection may use its accessible client or lead. Return only
  permitted fields and relationships; do not expose every model field or expand
  to accounts, subscriptions, reports or user administration because a shared
  search service can return those types.
- Writes operate on one selected record per action. An ambiguous name must be
  resolved before a write; never guess a parent or assignee. Limited lookup of
  eligible assignees and current allowed status values is supporting MVP work,
  not an administrative user-management feature. Preserve current backend and
  tenant-configured validation rather than introduce new business rules.
- Notes use existing note fields. Adding a note preserves existing content;
  explicitly requested replacement must show that intent and protect against
  stale updates. Interactions are recorded against an authorized existing parent.
  No new standalone notes product or automated outreach is required.
- MVP creation covers leads, contacts, projects and interactions. Client creation,
  lead-to-client conversion, deletion/restoration, bulk actions, file tools,
  report tools and platform/user administration are not included. Ask before
  adding any of them; existing CRM availability is not authorization to expose
  them to ChatGPT.

Exact tool names and field contracts are implementation choices within these
boundaries. Existing read-only connections must keep working with their original
permissions; adding tools does not silently expand an existing grant.

## Implementation order and acceptance criteria

### Customer experience requirement — approved October 7, 2026

The user explicitly confirmed that the finished experience must be easy: ask
ChatGPT to retrieve CRM information and receive it; ask to add a record and have
it saved, with only necessary clarification and approval. This is an acceptance
requirement for the locked workflows, not a deferred UI enhancement. It authorizes
the supporting connection and approval-flow changes, not additional CRM capabilities.

- Connect the intended PathSix account through normal sign-in and consent once.
  Routine use must retain that connection and its approved permissions across
  token refresh and new conversations. Reauthorization remains appropriate after
  revocation, expiry requiring sign-in, or a requested permission change.
- Retrieve authorized records from natural-language requests without manual
  scope editing, reinstalling duplicate apps, refreshing tool catalogs, or
  operator assistance. Ambiguous records require a concise clarification.
- Complete routine writes within the ChatGPT conversation, including a concise
  review and approval when required, and return the saved result automatically.
  Repeated CRM logins, separate review tabs and a further prompt just to check
  whether the save worked are not the accepted normal workflow. Target one
  explicit approval of the exact action; record any unavoidable host prompts.
- Preserve scope and record authorization, exact-action confirmation, atomic
  receipts/audits, replay protection and conflict handling. A model assertion
  of approval or a tool annotation is not a substitute for the approval boundary.
- Verify the normal path on desktop and phone, including a fresh connection,
  returning use, cancellation, one successful save, and recovery after a lost
  response. Record actual prompts/clicks and host limitations. Manual diagnostic
  workarounds and eventual OpenAI review do not satisfy this requirement.

The currently deployed external review page is an interim implementation. A
minimal review inside ChatGPT is an implementation candidate supported by the
[official UI documentation](https://developers.openai.com/plugins/build/chatgpt-ui)
and [tool/UI metadata reference](https://developers.openai.com/plugins/reference).
First verify the host's approval contract and how the server binds a genuine
user decision to the existing proposal and authenticated owner. UI-only tool
visibility and hidden result metadata are documented mechanisms, not by themselves
proof that the current server can trust a click. Do not remove the working
confirmation boundary before its replacement passes the security and live-flow
checks. See the [server guidance](https://developers.openai.com/plugins/build/mcp-server)
and [authentication guidance](https://developers.openai.com/plugins/build/auth).

### Implementation sequence

1. Build one complete lead-creation workflow in staging: find relevant records,
   collect required fields, review the proposed lead, obtain explicit approval,
   create it once and return the saved result. Reuse LeadService rather than
   duplicate its validation or authorization in the MCP adapter.
2. Establish the shared write boundary in that increment. Existing read grants
   must remain read-only; added permissions require fresh consent. Authenticate
   and recheck current membership, record access, scopes and revocation at execution.
   Confirmation must bind to the exact proposed action and current caller; a
   model-supplied approval boolean alone is not proof of user confirmation.
3. Persist an idempotency receipt with the business change and audit in one
   transaction. Repeated or concurrent requests must not create another record.
   Reusing a key with different inputs must fail. A lost response after commit
   must have a recoverable outcome. Do not automatically retry ambiguous writes
   or send duplicate notifications.
4. Expand to contacts, projects and interactions, then updates, assignments,
   statuses and notes. Updates must detect intervening edits from both the CRM
   and MCP so stale proposals cannot overwrite newer work. Parent changes and
   assignment changes must retain existing visibility and tenant rules.
5. Verify each increment with isolated PostgreSQL/RLS tests and synthetic staging
   workflows. Cover revoked/expired/read-only grants, foreign or inaccessible
   records, invalid parents/assignees, concurrent/replayed requests, stale updates,
   rollback/audit failures and ambiguous response recovery. Include prompt-driven
   attempts to turn CRM text into authorization. Preserve existing read and web
   regressions. Run real ChatGPT end-to-end checks before calling a workflow ready.
6. Complete production readiness and rollout verification, then prepare the public
   package, listing/policy links, reviewer access, demonstrations and positive and
   negative workflow cases. Submit the implemented capability set for OpenAI review
   and publish after approval.

The immediate engineering milestone is the lead-creation workflow plus the shared
write protections above. This document change itself enables no new runtime
capabilities and is not evidence of a migration, deployment or submission.

The [lead-creation increment](mcp-lead-creation.md) is implemented on
`codex/mvp-lead-creation` with local backend, browser and PostgreSQL CI verification. Its report
tracks PostgreSQL and staging validation separately. This is progress toward
step 1, not completion of the MVP or permission to start a deferred feature.

## Definition of MVP build completion

Mark a criterion complete only with linked test or release evidence. The earlier
594-test milestone is the read-only baseline, not proof of the future write build.

- [ ] All five workflow groups in the locked capability table work end to end in
  ChatGPT against synthetic staging records, including cancellation, invalid
  input, ambiguous selection and insufficient permissions. Confirm resulting
  records in the CRM. Verify connection, consent and confirmation on phone and
  desktop, with ordinary-user and administrator permission cases.
- [ ] The approved customer experience above works through normal ChatGPT use:
  persistent account connection, natural-language reads, writes and necessary
  approval in the conversation, and automatic saved-result reporting. Fresh
  connection and returning-use trials pass on phone and desktop without manual
  scope fixes or routine external review/login steps. Document any host limitation
  for the user's decision rather than silently accepting a different workflow.
- [ ] Write consent, action confirmation, revocation, audit, idempotency and stale
  update protection meet the implementation criteria. Repeated/concurrent calls
  and lost responses cannot silently duplicate actions or overwrite newer work.
  Revoked or insufficient access cannot recover private data through a receipt.
  Notification failures are distinguishable from the outcome of a saved record.
- [ ] Relevant local and PostgreSQL/RLS CI checks pass, including existing web/read
  regressions and the new write failure cases. Staging cleanup is verified and
  any unrelated known failures are explicitly explained, not called a clean pass.
  No known blocking authorization, data-integrity or workflow defect remains.
- [ ] Operational preparation is complete for this tool set: shared abuse limits
  for OAuth/MCP entry points, privacy-preserving error/audit monitoring, actionable
  alerts, bounded audit/receipt retention consistent with replay protection, and
  a short incident/revocation/rollback runbook. Diagnose and fix database failures
  that block these workflows or trustworthy validation. This does not require an
  unrelated infrastructure redesign or a general scaling/performance program.
- [ ] A reproducible release and necessary migrations are rehearsed with rollback
  and backup/recovery checks for affected data. Verify the intended production
  OAuth client, configuration and deployed code, preserve existing production
  fixes, and confirm customer linking and basic reads. Validate writes using
  isolated synthetic records, not customer records. Record rollout evidence and
  unresolved operational limits rather than infer readiness from local tests.
- [ ] The submission package describes exactly the tested tools and permissions.
  Listing/policy/support information, reviewer access, positive and negative
  examples, and a demonstration are ready; claimed test cases have actually run.

When these checks are met, the build is ready for submission. Submission, OpenAI's
review decision and publication are separate release statuses and must be reported
accurately. A review request that requires extra capabilities or broader work
outside the locked scope requires the user's decision; ordinary defect repairs
remain allowed. Waiting for external review does not authorize backlog work.

## Future consideration

**Recorded only; not approved work and not additional MVP completion criteria.**
For new entries, record the observation, its impact, supporting evidence and any
user decision. Keep this section at the bottom. If an item becomes a concrete MVP
defect, a bounded repair is allowed under the rules above; otherwise ask before
moving it into the build.

| Item | Reason to revisit | Current disposition |
| --- | --- | --- |
| Client creation and lead-to-client conversion tools | Extend the sales lifecycle beyond the approved create/update set | Requires a scope decision |
| Delete, restore, bulk/import, file, reporting and administrative tools | Additional CRM workflows beyond this MVP | Requires a scope decision; file tools also need durable storage |
| Dynamic client registration/CIMD and broader AI-client support | Improve interoperability beyond the configured ChatGPT connection | Deferred unless required for the agreed connection; ask before expanding |
| Full resource/cost inventory and platform-wide recovery work | Older operational roadmap extends beyond recovery checks for this release | Deferred; release-specific recovery checks remain in the MVP |
| Global account-number uniqueness and polymorphic activity references | Known model-design questions in the isolation reports | Record specific defects if encountered; no general model redesign assigned |
| Durable file reconciliation, import notification infrastructure and platform workers | Support workflows not exposed by this MVP | Deferred; current MVP assignment side effects must still behave correctly |
| Broader scaling, generalized abstractions and UI redesign | Potential future improvements after real usage | Not part of this build |
| CI action/runtime maintenance | [Lead-creation CI](https://github.com/boonewh/pathsix-backend/actions/runs/37543228687) warns that checkout/setup-python target deprecated Node 20 and that ubuntu-latest is scheduled to change | CI passed; record for later maintenance, not an expansion of this milestone |
