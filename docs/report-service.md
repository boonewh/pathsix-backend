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

Local full-suite and PostgreSQL CI results are recorded in the associated PR.
Staging rollout is pending for this increment. It requires no migration or frontend
change. The already verified client-service staging v30 image is its rollback target:
`registry.fly.io/pathsixsolutions-backend-staging:4301ba49627e5cc0bd566ae0a40f967c20e400e9`.
