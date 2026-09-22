# Production database errors — 2026-09-16

Initial read-only review of Sentry and Fly metadata, followed by the user-authorized
production repair described below. No CRM records or database schema were changed.

## Observed incident

- At 11:54:56 UTC (06:54:56 America/Chicago), Sentry issue PYTHON-QUART-18
  recorded three production auth database failure events. The inspected event
  e0a29929985d4b8ea571a827d527bc02 concerns GET /api/clients/ on application
  machine d894111b636938. The issue's grouped culprit is leads.list_leads;
  do not assume all three events concern the same endpoint.
- At 11:54:57 UTC, PATHSIX-FRONTEND-1T recorded three production API database
  error events. Inspected event aafb1ee48aca4581a93ed021433bbadd reports HTTP
  500 for GET /api/projects/?page=1&per_page=10&sort=oldest from /projects.
- The backend event displayed a logging template with literal %s placeholders.
  Its retrieved details did not expose the exception class, SQLSTATE,
  connection-invalidated flag, or retry decision. The underlying database
  failure cannot be classified from this event alone.

## Live application configuration

Fly reported one production application machine, d894111b636938, started in
ord with one shared CPU and 1024 MB RAM. Its deployed service has autostop=false,
autostart=true, and min_machines_running=1. Its latest recorded start event was
2026-09-09T21:40:10.751Z. These observations do not support a routine application
VM wake-from-idle cold start at the incident time. They do not exclude an
application-process restart, database interruption, or stale database connection.

## Follow-up: database cold start correlated with the incident

Read-only Fly logs and machine metadata for pathsixsolutions-db establish that
the separate production database machine 185925b476e108 has one shared CPU,
256 MB RAM, autostart enabled, and FLY_SCALE_TO_ZERO="1h". It was stopped when
inspected. The application VM's always-on setting does not apply to this machine.

| UTC on September 16 | Evidence |
| --- | --- |
| 11:54:53.408 | Database machine start requested by proxy. |
| 11:54:54.751 | Machine entered started state. |
| 11:54:55 | PostgreSQL started; reported prior shutdown at September 15 22:18:58 UTC. |
| 11:54:55 | PostgreSQL ready on 5433; Fly proxy also logged a broken pipe. |
| 11:54:56 | HAProxy worker terminated and restarted; Fly logged three refused connections on port 5432. Backend Sentry errors occurred in this same second. |
| 11:54:57 | HAProxy restarted successfully; frontend Sentry HTTP 500 events occurred. |
| 12:54:55–56 | Connection-count check, normal exit code 0, and machine stopped without restart. |

This provides strong evidence that this production incident was a database
wake-up/readiness interruption. It does not establish that every prior production
error shares this cause. Generic resource-limit health messages during startup
and shutdown do not, by themselves, establish memory exhaustion.

The recommended change was to disable scale-to-zero on the production database
while preserving its image, volume, and other configuration. The authorized repair
below applies it. Running compute time increases; RAM size does not change.

The application's retained log tail only covered later health checks and did
not include the morning incident. The underlying driver exception and why its
diagnostic values were absent from the retrieved Sentry event remain unverified.
The diagnostic presentation correction and its verification are described below.

The earlier comparison confused the application and database tiers: staging's two
application VMs have 1024 MB each, but its separate database machine
830376c7157038 was verified on September 16 to still have 256 MB. The proposed
512 MB staging database experiment remains relevant and unperformed in this review.

## Authorized repair and verification — September 16

- Removed FLY_SCALE_TO_ZERO from the production database's exact machine config
  through the Machines API, then started the existing machine at about 16:14:49 UTC.
  The preliminary CLI update merged the old variable back in and was not accepted
  as success; verification caught it before the API update.
- Preserved the PostgreSQL 17.2 image digest, 256 MB RAM, CPU, volume
  vol_vx2jzeg776zonzjr, ports, checks, and restart policy. Saved before/after
  configuration under docs/diagnostics/production-db-config-*-2026-09-16.json.
- All database health checks pass; the shutdown variable remains absent.
- Prepared auth diagnostics from production branch codex/production-read-recovery,
  separately from the staging checkout, on branch codex/production-db-always-on
  in temp/production-db-hotfix. The logging summary is rendered without %s
  placeholders, with incident, endpoint, method, attempt, exception/driver classes,
  SQLSTATE, disconnect state, and retry state in extra.database_failure. SQL,
  exception messages, parameters, and credentials are excluded. Retry behavior
  and response behavior are unchanged.
- 19 focused tests passed: existing auth/recovery and Activity tests plus an actual
  Sentry SDK 2.19.0 serialization test asserting diagnostic fields and privacy.
- Deployed a one-file overlay built FROM the exact prior production image digest
  fec38111a662efb2ea6feb9acf11c10e8d7fad8181356705190935ced71fc0c3.
  New image: registry.fly.io/pathsixsolutions-backend:auth-diagnostics-20260916,
  digest c1f3e76cbb4a411376827b078aeb84838085fdb4a1cfbd447d25c80a9e0915fb.
  Fly waited for the existing application machine's health check to pass.
- At 16:40:39 UTC, SELECT 1 and three concurrent authentication-path reads passed
  inside production. A short-lived token for nonexistent user -1 exercised lookup
  and produced the expected User not found responses without invoking business
  handlers or reading CRM records. Public /api/health returned status ok.
- The deployed auth_utils.py SHA-256 matches the tested file:
  04a220ea60d64b199d19a2041146f68839599c6a12d0044637eb300a1132c613.
- Fly SSH printed successful checks then its previously observed Windows
  'The handle is invalid' CLI exit message. Independent health/config checks pass.
- One-time follow-up automation verify-production-database-stays-awake is scheduled
  in this thread. It must inspect machine state before any database query, after
  17:20 UTC, and pause itself after reporting. Until then, staying awake beyond the
  old one-hour boundary remains pending, not proven.

### Rollback and follow-up

Application rollback image: registry.fly.io/pathsixsolutions-backend:deployment-01M241SA6JN0057QEC3NM7W90K.
Database rollback config is saved, but restoring it deliberately re-enables the
known cold-start behavior. Future production source deployments must include the
hotfix branch's auth diagnostics change. Staging changes were not deployed.

During an unsuccessful API attempt, PowerShell's header-format error included a
Fly authentication token in tool output. It was not saved in repository files.
Treat that credential as exposed and revoke/rotate it; subsequent API calls suppress
credential-bearing exception text. Do not copy the token into incident records.

## Sources

- https://pathsix-web-design.sentry.io/issues/PYTHON-QUART-18
- https://pathsix-web-design.sentry.io/issues/PATHSIX-FRONTEND-1T
- Read-only Fly machine list for pathsixsolutions-backend.
- Read-only Fly logs and machine list for pathsixsolutions-db.
- https://fly.io/docs/postgres/managing/scale-to-zero/
- See staging-connection-investigation.md for the separate staging evidence.
