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

The test fixture also needs cleanup registered before setup: its current yield-only
teardown cannot remove a schema when setup itself fails. That is a separate known
test-harness improvement, not an explanation for the disconnect. The previously
orphaned schema was already removed and verified in the v23 handoff.
