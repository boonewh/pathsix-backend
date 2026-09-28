# MCP readiness status — September 28, 2026

This reconciles the historical September 6 frontend roadmap against the integrated
backend. The goal remains a remote MCP adapter over the same tenant-bound services
as the CRM, with delegated user consent and read-only tools before writes.

## Baseline and release status

Work resumed from staging `aa1d830`, which merges the September 22 handoff.
The current verified live backend is **v32 / `2149bb6`** after the client, report and storage
service increments. PostgreSQL/RLS CI passes **299 tests with zero skips**; both
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
This handoff is documentation only; v32's application source remains authoritative.

## Reconciled gates

| Gate | Status | Evidence and remaining work |
| --- | --- | --- |
| 0: operational baseline | Partial | Integrated staging source and CI are reproducible. Staging DB sleep is documented as one hour. Original dirty checkouts are preserved separately. Full resource/cost inventory, restore drill and earlier staging DB-stall investigation remain open. |
| 1: immediate REST security | Completed documented milestone | Current server-side roles and active user/tenant checks, parent authorization, admin reports/import, protected calendars, disabled tenant backup API, JWT validation and reset protections. See reliability-security-2026-09-06.md. |
| 2: structural isolation | Substantial progress; incomplete | Trusted per-operation web Principal, tenant services, restricted DB role, tenant FKs/indexes, 30 same-tenant relationship FKs, parent rules and forced RLS on 14 tables. Connection/grant identity and remaining service paths still need work. |
| 3: adversarial isolation proof | Partial | PostgreSQL CI exercises restricted-role RLS and cross-tenant HTTP/service boundaries. File/object paths, remaining route/worker paths and intentional missing-policy/predicate failure coverage still need completion. Passing existing tests does not certify every roadmap invariant. |
| 4: delegated AI authorization | Not implemented | Authorization server selection, discovery, PKCE consent, audience-bound scoped tokens, refresh/revocation, connection/grant storage and connected-AI management UI remain. Web Principal is not delegated authorization. |
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
- CSV lead creation already calls LeadService with row savepoints after the
  September 22 integration. Import orchestration/assignee lookup remains in the
  route; do not describe the whole import workflow as extracted.
- All report reads now use an admin-only ReportService, including
  the legacy summary alias and Sales Activity adapter. StorageService owns file
  metadata/object access with current-role checks, confined paths and ordinary-failure
  compensation. User administration and preferences still contain route-owned queries.
  Background tasks require explicit identity review.
  Global backup/restore is a platform operation; keep it outside tenant/MCP APIs.
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
4. Next implementation: import orchestration, user/preferences and background-context
   boundaries with focused adversarial tests. Keep the operational DB-stall issue
   visible; do not infer it is fixed from a successful CI run.
5. Reconcile Gate 2/3 exit criteria, then implement delegated authorization and a
   bounded read-only MCP pilot. No user AI access is enabled by this increment.
