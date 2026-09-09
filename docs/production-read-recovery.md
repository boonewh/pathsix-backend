# Production database read recovery — 2026-09-09

Prepared directly from production main `654623a`; no staging security/RLS changes included.

The September 9 Projects incident produced simultaneous 500s for projects, clients,
leads and preferences. Authentication's database lookup swallowed the original
exception, so the exact historical database failure cannot be established. The
thread-local SQLAlchemy scoped session was independently unsafe for concurrent
Quart requests on the same event-loop thread.

## Changes and limits

- Each `SessionLocal()` call now creates an independent session. Existing pool
  pre-ping, recycling and connection limits are retained.
- GET/HEAD authentication lookups retry once only when SQLAlchemy marks a DBAPI
  connection invalidated. The failed session closes before a 250 ms async backoff.
- Route handlers are never replayed, including GET handlers that record views.
  POST/PUT/PATCH/DELETE authentication lookups do not retry either.
- Exhausted disconnects return 503 with a safe message, retryable flag and incident
  ID. Other database errors remain 500. Diagnostics log endpoint, method, exception
  class, driver class, SQLSTATE, disconnect and retry state; not SQL/parameters or
  the raw driver error. Existing Sentry logging integration receives error logs.
- Frontend shared API reads display an actual pending-request spinner. No artificial
  wait and no frontend automatic retries. Projects waits for all related requests,
  ignores cancelled/outdated loads and offers a single visible manual retry after
  failure. Failed loads do not display zero/stale totals as current results.

## Verification

- 10 recovery/auth tests and 8 Activity report tests pass.
- Complete backend suite: 23 pass; the two previously known lead schema validation
  tests still fail (invalid status/type acceptance, unrelated to this fix).
- Frontend typecheck and production build pass; all 8 browser tests pass, including
  desktop/mobile failure recovery, concurrent loading, cancellation, error body
  preservation, unauthorized logout and Activity report regressions.
- No schema migration, DNS, email secret or staging configuration changes.

## Rollback

Revert the isolated application commits on main and deploy their predecessors.
No data migration reversal is needed. Keep the Activity report release intact.
