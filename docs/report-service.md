# Report service — September 28, 2026

ReportService owns all 15 report read implementations. The summary POST aliases
the same implementation as the legacy GET. HTTP adapters authenticate a fresh
Principal, parse request parameters, translate domain validation/authorization
errors and close their sessions. They contain no model or ORM query logic.

The service requires an admin principal before any report can be called. It binds
the identity to the session and scopes root entities, joins, subqueries and lazy
relationships without relying on HTTP hooks. Outer joins retain clients/leads
without interactions and interactions having only one parent. Joined tenant
predicates stay in JOIN conditions rather than accidentally requiring both parent
types in WHERE.

Financial/current-record queries exclude cross-tenant or malformed project,
interaction and subscription parents, including when RLS is bypassed by a test
administrator. Display names and user-filter subqueries are tenant-scoped. The
Sales Activity implementation retains its explicit tenant predicates and historical
actor/event semantics; the adapter now obtains its tenant from ReportService.

## Contracts and limits

- Existing response fields, formulas, monthly/yearly revenue treatment, date
  columns and legacy inclusion of deleted records are retained. Historical deleted
  won leads remain in conversion/source reports. The pipeline user filter still
  affects leads only. Legacy engagement counts are not relabeled as actor history.
- Existing report date filters remain inclusive timestamps; Sales Activity retains
  its separate strict YYYY-MM-DD inclusive-day convention. Invalid or reversed
  ranges return 400; timezone-bearing legacy input normalizes to UTC.
- Revenue list limits accept 1–200; follow-up/renewal windows accept 0–3650 days;
  user filters require positive integer IDs; Sales Activity pages must be positive.
  Invalid parameters produce 400 rather than unbounded reads or arithmetic errors.
- Duration calculations use the actual bound database dialect, enabling both
  PostgreSQL and SQLite test sessions without global connection-string guesses.
  PostgreSQL Decimal averages are normalized to JSON numbers; previously Quart
  serialized those duration fields as strings, unlike the SQLite path.
- Reads never commit or create view/audit records. This is still a web report
  boundary: unpaginated legacy reports must be bounded/minimized before MCP exposure,
  and OAuth grants/scopes remain unimplemented.

## Validation

Six new focused cases cover all report methods directly and over HTTP, with both
the administrator fixture connection and restricted runtime factory. They check
two-tenant aggregate/name boundaries, malformed links without HTTP/RLS, outer joins,
known report totals/durations, current-role denial, pure reads, validation and
historical conversion behavior. Existing Sales Activity and staging integration
regressions also pass (18 focused cases total).

Full local suite: **245 passed, 29 PostgreSQL-only skipped**. After the small
validation and Decimal normalization follow-ups, the six focused report cases
passed again. Final [PR #15 CI](https://github.com/boonewh/pathsix-backend/actions/runs/36483870143)
passed **274 tests with zero skips** against PostgreSQL 18 and restricted-role RLS.
The first PostgreSQL run exposed Decimal serialization in two duration fields;
the correction is included in application commit `4c489e6`.
[Merged staging CI](https://github.com/boonewh/pathsix-backend/actions/runs/36484271988)
also passed. Compile and diff checks passed.

## Verified staging rollout

PR #15 merged as `30c6ab2476648535c5922e7da40a70e6a7ffa525`. Fly **staging v31**
deployed that revision to both existing machines, `82549ec7079218` and
`7812092ae397d8`. Both returned passing health checks. Image digest:
`sha256:3304b4b7cd5ee3d515b775a284506dbc30614d52500df3b0bcb0d4a230e5976d`.

Live login, six health/protected reads, all **15 report GET response comparisons**,
the summary POST alias and **nine invalid-request checks** passed. Every report
response matched the saved predeployment baseline. This was HTTP verification,
not a new browser run. No report smoke records were created.

Runtime inspection confirmed `pathsix_crm_staging_runtime`, `rolsuper=false`,
`rolbypassrls=false`, `CRM_RLS_ENABLED=1` and the expected application revision.
Both machines retain their original 1 GB VM and HTTP-service/sleep settings.
No migration, frontend deployment, main update or production deployment was made.
All 21 previously modified/untracked files retained their saved hashes and all
three original checkout HEADs were independently verified unchanged.

Local source archives, safe runtime/machine evidence and smoke scripts/results are
under `temp/client-service-rollout/` and `temp/report-service-rollout/`.
The earlier staging database-stall investigation remains open; passing CI and this
bounded smoke run do not establish its cause or resolution.

The verified client-service staging v30 image is the rollback target:
`registry.fly.io/pathsixsolutions-backend-staging:4301ba49627e5cc0bd566ae0a40f967c20e400e9`.
