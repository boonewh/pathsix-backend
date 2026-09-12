# Staging connection investigation — 2026-09-08

Read-only investigation after the v23 test setup interruptions. Production was not
accessed; database credentials, machine sizes, app configuration and schema were
not changed. No full suite was rerun during this investigation.

## Evidence

- Staging database machine 830376c7157038 has remained started since July 31 UTC.
  PostgreSQL reports pg_postmaster_start_time 2026-07-31 04:35:29.611154+00:00.
  This argues against a PostgreSQL crash/restart during the September 8 failures.
- Kernel query returned no retained out-of-memory kill entries. This is limited
  evidence, not proof that memory pressure never occurred.
- Machine has 256 MB configured RAM; current MemAvailable was about 45 MB. Memory
  pressure averages were near zero during inspection. shared_buffers is 25 MB,
  work_mem 4 MB and maintenance_work_mem 64 MB. No capacity change is justified by
  this snapshot alone.
- max_connections is 300; the inspection saw 14 PostgreSQL processes/connections,
  including internal workers, one active inspection and five idle sessions.
  idle_session_timeout, idle_in_transaction_session_timeout and statement_timeout
  are all zero. PostgreSQL's TCP keepalive idle setting is 7200 seconds.
- PostgreSQL writes logs to stderr; logging_collector is off and the packaged
  /var/log/postgresql file is empty. The available Fly log tail is dominated by
  expected constraint failures from adversarial tests. Those are not CRM defects.
- Fly proxy logs have broken-pipe messages at 23:34:30 UTC. They do not establish
  the cause of the earlier setup interruption; do not treat them as a correlated
  root cause without matching timestamps and connection evidence.
- A bounded comparison ran twelve read-only subscriptions-table reflection checks
  through each of the Flycast and .internal hostnames on their existing port.
  Both passed (0.19 and 2.20 seconds total respectively). This is a small current
  health check, not a reproduction of schema-creation load or proof of reliability.
  The .internal hostname check does not prove that every database-side proxy was
  bypassed. Application connection configuration remains unchanged.

## Conclusion and next diagnostic step

Root cause remains unresolved. There is no evidence here requiring a larger machine,
and neither a database restart nor PostgreSQL idle timeout explains the captured
failure. Do not declare the prior interrupted suite clean or add write retries.

At the next necessary PostgreSQL validation, capture timestamped connection failure
metadata and a concurrent filtered Fly log stream, retain PostgreSQL start time and
memory/connection snapshots, and run serially. Stop on the first setup disconnect
instead of cascading through more fixtures. Keep credentials and SQL parameters out
of diagnostic output. Compare network paths only if that captured evidence points
to routing; do not change the app connection URL merely because a small probe passes.

The prior yield-only fixture teardown could not remove a schema when setup itself
failed. The follow-up below fixes that separate harness defect; it does not explain
the disconnect. The previously orphaned schema was removed in the v23 handoff.

## Harness follow-up — 7c48a07

The CRM fixture registers its finalizer before CREATE SCHEMA. Cleanup disposes the
test pool, starts a fresh operator connection and drops only that fixture's generated
schema with IF EXISTS. Cleanup has five-second lock and fifteen-second statement
timeouts, always disposes the operator pool and reports failure rather than hiding
it. An ongoing database outage can still prevent cleanup; the finalizer does not
guarantee success during an outage and never retries application writes.

Set CRM_TEST_DIAGNOSTICS=1 to emit UTC JSON records for test setup/call/teardown,
outcome, exception class, SQLSTATE if available and connection-invalidated status.
No exception messages, SQL or parameters are emitted by this hook. Continue using
--tb=no for remote runs because ordinary pytest tracebacks are a separate source
of sensitive output. Use -x to stop at the first failure and run serially.

Five focused checks passed locally and on staging PostgreSQL, including a synthetic
exception immediately after schema creation and independent verification that its
schema was removed. The other checks cover diagnostic privacy and the three lead
assignment regressions (email stubbed). Staging interval was 2026-09-09
00:47:06–00:47:11 UTC; pytest reported 5 passed in 4.81 seconds. Test-schema count
was zero before and after, connection counts were 15 then 14, and PostgreSQL start
time remained July 31. A concurrent filtered Fly stream reported no new matching
connection/restart/OOM events in this interval; its only matching entries were the
older September 8 23:34:30 proxy messages. This narrow check does not resolve the
original intermittent connection issue or replace full validation of future changes.

Only three test files were copied to /tmp/crm-harness-7c48a07 on the existing staging
backend. Temporary pytest dependencies were restored before the check. Application
release remains v23 / c23b53b; no app deployment, database migration, machine resize,
configuration change or production access occurred. The filtered log process was
stopped after the check. Use these diagnostics on the next necessary validation.


## Recurrence — 2026-09-12, Activity/subscription rollout

Staging v28 / de3f468. Combined PostgreSQL tests stopped during subscription
invalid_create[body1] setup after Activity cases completed. Recovery passed three
cases before invalid_create[body4] setup and teardown errored. The first run's
post-test count on a retained admin engine also encountered a closed connection;
the recovery's new-engine snapshot failed while connecting through .flycast.
Do not attribute all errors solely to reuse of an idle pooled connection.

Cleanup failed despite early finalizer registration. Exact orphan schemas
security_test_2f0ee681a4d24b8ead6b3545b16e4b4e and
security_test_aa929a67bd5245399fe4f14a3c4fb6e5 were each identified and removed.
No public data was removed. Subsequent fresh probes through .flycast and .internal
both succeeded; PostgreSQL postmaster time remained 2026-07-31 04:35:29 UTC and
connection counts were low (13 at the comparison). Filtered logs captured no match.
These observations do not establish root cause or prove either path reliable.

The final four tests passed through .internal in 5.90 seconds. In aggregate all
34 cases passed across attempts, not one clean suite. Final direct-address checks
confirmed zero test schemas, original demo counts, fourteen forced RLS tables and
unscoped runtime read denial. Runtime DATABASE_URL remains .flycast; no app config,
resources or automatic retry policy changed. Live subscription and Activity API
checks passed separately. Prioritize capturing durable redacted test-phase results
(including cleanup failures) and correlated Fly proxy/database diagnostics before
more broad staging testing. Do not repeatedly rerun suites or claim this is fixed.


## Diagnosis — 2026-09-12 (read-only; supersedes the earlier lack of evidence)

The latest two failures now have a correlated infrastructure explanation. Retained
Fly logs, fetched with --no-tail and a broader filter, contain events missed by the
previous streamed filter. That earlier filter omitted generic health failures and
HAProxy Layer7/backend messages, so its silence was not evidence of healthy routing.

| UTC | Observed staging event |
| --- | --- |
| 23:35:06 | VM health failed: resource limits |
| 23:35:25–26 | Role and PostgreSQL health checks failed |
| 23:35:31–33 | PostgreSQL client lost; all HAProxy bk_db targets DOWN after roughly 5–6 second Layer7 timeouts; no server available |
| 23:35:37 | Proxy targets healthy again |
| 23:38:34–36 | VM/role/database health failed again; proxy targets DOWN after roughly 5.2 second Layer7 timeouts; no server available |
| 23:38:41 | Proxy targets healthy again |

Historical staging metrics for 23:33–23:41 UTC are retained under docs/diagnostics.
The VM's CPU burst balance stayed 49,216–50,000 centiseconds and CPU throttle stayed
zero. Disk-wait CPU increments reached 1,257 and 1,388 centiseconds in 15-second
samples (approximately 84% and 93%). Outstanding I/O on vda reached 49 in the first
window and 58–61 in the second; available RAM fell to about 27.5 MiB and 23.3 MiB,
then recovered. The PostgreSQL data volume vdc had zero sampled outstanding I/O;
its capacity was only 17% used at the later guest check. CPU quota exhaustion,
connection-count saturation and a full volume are not supported by these observations.

The VM is shared-cpu-1x with 256 MB configured, about 207 MiB visible to the guest,
no swap and roughly 45 MiB available when later idle. Guest memory and I/O pressure
were still elevated in the five-minute averages. Guest VM counters showed substantial
file-page reclaim/refaults, 114,728 major faults and zero OOM kills since boot.
Those cumulative counters alone do not timestamp the incident. Exported host-side
memory-pressure metrics were all zero and differ from the guest /proc/pressure
readings; do not use them as proof that guest memory was unconstrained. vdb is mounted
as the writable upper layer, vdc is /data, and vda is the other 8 GB non-data device (the image/lower-layer path is suspected).

Confirmed failure mechanism: resource/I/O stalls cause database health checks to
time out; HAProxy marks every target unavailable and connections are lost, including
fresh connections and cleanup. PostgreSQL need not restart for this to happen.
Likely underlying trigger: limited guest RAM causes file-cache reclaim/refault I/O
under repeated schema-heavy fixture setup. This is a strong working hypothesis,
not proof that RAM alone explains the storage latency. No controlled resize or
reproduction under changed resources was performed, and earlier September incidents
are not proven to share this cause.

.internal:5432 bypasses Flycast routing but still uses the database's HAProxy
listener. Therefore its four passing tests are not evidence that changing the app
hostname fixes this. Keep the current URL; do not add application write retries,
disable health checks, or lengthen timeouts to conceal resource starvation.

Recommended next experiment: increase only the existing staging database VM from
256 MB to 512 MB, then run one bounded, serial PostgreSQL validation with durable
redacted phase logs and before/during/after memory, I/O and health metrics. Success
means no proxy health drops/disconnects/orphan schemas, not merely a later rerun pass.
If stalls remain, examine root-image/host I/O and Fly support diagnostics rather than
assuming more RAM always solves it. A memory increase changes recurring cost and
restarts the staging database; it has NOT been applied by this diagnostic task.

This diagnosis ran no database test suites, migrations, schema changes, deployments,
resizes or production operations. Metrics/CLI credentials stayed in memory and are
not in the saved artifacts. Staging app remains v28 / de3f468.

References: https://fly.io/docs/monitoring/metrics/ (CPU counter units and metrics API),
https://fly.io/docs/postgres/advanced-guides/high-availability-and-global-replication/
(port 5432 proxy), and https://fly.io/docs/postgres/managing/scaling/ (VM memory scaling).
