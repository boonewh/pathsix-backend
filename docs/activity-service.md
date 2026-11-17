# Recent Activity service — 2026-09-12

ActivityService owns all Recent Activity database reads. It binds the current
Principal, selects only that user's tenant-scoped logs, and rechecks current record
permissions before returning names or links. Historic activity does not grant access.

Clients/leads use shared creator-or-assignee/admin access. Projects use the shared
project predicate, including direct assignment and active valid parents. Accounts
inherit active-client access. Deleted/foreign/malformed records are excluded even
for admins; admins still see only their own activity history. The service is a pure
read and never records a view or commits.

Response fields, latest-per-entity grouping, descending timestamp order and client
links for accounts remain. Positive limits are capped at 50; invalid/zero/negative
limits return 400. As before, the limit selects recent log groups before unavailable
records are filtered, so the response may contain fewer than the requested count.
No migration is needed; polymorphic log constraints remain separate roadmap work.

## Validation

Local: 10 focused passes, then 172 passed / 29 PostgreSQL-only skipped in the full
local suite (63.79 seconds). Coverage includes access removal after assignment,
project assignment overriding parent access, own-user/tenant log scope, foreign and
malformed entity IDs, deleted records, grouping, pure reads and invalid limits.
Staging v28 / de3f468 includes both Recent Activity and subscriptions. Live
subscription lifecycle: 16 HTTP checks passed including cleanup. Live Activity:
19 HTTP checks passed, including four viewed entity types, invalid-limit rejection,
removal after deletion and cleanup. These are API smoke checks, not browser UI tests.

All 34 focused Activity/Subscription PostgreSQL cases passed across initial and
targeted runs, NOT a clean uninterrupted run. The first run stopped during setup
of subscription invalid_create[body1]; a later seven-case recovery passed three,
then stopped with setup and teardown errors at invalid_create[body4]. Both attempts
had server-closed-connection errors and left one temporary schema each; both exact
schemas were independently identified and removed. The last four cases passed in
5.90 seconds through the staging .internal address. Earlier attempts used .flycast.
No application database URL, timeout, retry, machine size or count was changed.

Final verification through .internal: original two clients/two leads, zero accounts,
contacts/projects/interactions/subscriptions, zero test schemas, parent_link_rules,
fourteen forced RLS tables, CRM_RLS_ENABLED=1 and zero unscoped runtime reads from
clients/leads/users/tenants. Both address probes later succeeded and PostgreSQL
uptime remained July 31. The filtered log stream did not capture matching errors;
that does not negate the observed disconnects or establish their cause.

Production and frontend deployments remain unchanged. Prioritize the repeated
staging connection/cleanup failure before continuing the remaining services. See
docs/staging-connection-investigation.md. All local code changes are committed;
no AI access has been enabled.
