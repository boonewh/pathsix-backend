# Current identity and platform-job boundary — September 29, 2026

IdentityService owns `/me`, `/tenant/config` and authenticated password changes.
It accepts a trusted per-operation Principal and explicitly scopes the current user
and tenant, including without HTTP or database RLS. Login and signed password-reset
lookups remain authentication-boundary operations; they are not tenant services.

## Current-user behavior

- Both the user and tenant must still be active. Responses use current database
  roles, preserve the existing shapes and omit password hashes. Configuration
  responses are deep copies; HTTP responses carry `Cache-Control: no-store`.
- Password changes accept only the current user's identity, regardless of uploaded
  IDs. The current hash is refreshed under a PostgreSQL user-row lock before password
  verification, so two changes cannot both authenticate against the same old hash.
  The caller owns commit/rollback. Failed writes are not advertised as safe to retry.
- Non-object requests and missing/non-text passwords fail safely. New passwords over
  72 UTF-8 bytes are rejected. Existing password-strength policy and token lifetime
  are unchanged; this increment does not add session/token revocation.

## Background-operation findings and changes

The implemented background jobs are whole-database backup, restore and retention
cleanup. Their RQ queue, worker entry script and scheduled scripts are platform
operations. The old admin-backup HTTP blueprint remains unregistered. No tenant/MCP
endpoint or tenant background-job dispatcher is introduced.

Every job now checks authorization before creating a session, changing a record,
opening object storage or launching a subprocess. The scheduled-backup entry checks
before creating its pending record; RQ checks before listening, and jobs independently
recheck their own operation. Cleanup's script exits nonzero on denied access.

An operator must explicitly configure `CRM_PLATFORM_JOB_OPERATIONS` on a dedicated
privileged worker process. Accepted values are comma-separated `backup`, `restore`
and/or `cleanup`; absent, empty or unknown entries fail closed. Enabling backup does
not authorize restore. Never put this allowlist or privileged worker credentials on
the web/MCP deployment. This increment does not set the allowlist anywhere.

Authorization also requires a PostgreSQL connection whose actual role is superuser
or has BYPASSRLS, with no tenant/auth/reset context, and refuses any HTTP request
context even if the allowlist is configured. A tenant admin or an erroneously enabled
restricted web connection cannot satisfy that check. It grants no database privileges.
Database authorization failures return safe errors without connection/SQL details.

This is a fail-closed process boundary, not a complete platform operations system.
Dedicated least-privilege operator credentials, protected queue ownership, per-run
restore approval/audit, environment-specific object namespaces, crash recovery and
a restore drill still need review. Existing backup/restore internals were not executed
or certified; no dump, restore, cleanup or external object mutation occurs in validation.

## Validation and rollout

Focused tests cover direct/HTTP identity and tenant isolation, inactive identities,
independent config responses, forged IDs, password rollback/commit failures, stale
hash reload and concurrent changes. Platform checks prove entry denial before side
effects, operation allowlists, HTTP denial, database-role/context checks and safe
database errors. The full local suite passed **365 tests**, with **30 PostgreSQL-only
skips**. [PostgreSQL 18/RLS CI](https://github.com/boonewh/pathsix-backend/actions/runs/36631759633)
passed **395 tests with zero skips**, including concurrent password changes and
actual restricted-role rejection. All **36 focused tests passed** against deployed
staging code in disposable PostgreSQL schemas, including the concurrent-password
case. Positive password tests used isolated data; the digest of public users,
memberships and preferences was unchanged (including password hashes). Public counts
remained one user, one membership, zero preferences, two leads and 67 activities;
no test schemas remained. Platform operations were tested only by guarded
denials or mocked authorization, never by running real jobs.

The live HTTP checks passed for identity/config response compatibility, no-store
headers, malformed/wrong-current-password rejection and the absent backup API.
The pre/post identity/configuration fingerprint was unchanged. The temporary HTTP
checker was corrected to accept the unregistered endpoint's normal HTML 404.

Application revision `6c8ce189d42e9e06be73a8bee59b418a561801a7`, built from a clean Git
archive, runs as **staging v35**. Image digest:
`sha256:d9aa911279e3fabd3eae9b6409f4500365ddd83fdb15085cd4631045709eab8d`.
Both existing machines (`82549ec7079218`, `185777d6c554e8`) passed health checks with
unchanged capacity, service configuration and mounts. Runtime inspection confirmed
the restricted `pathsix_crm_staging_runtime` role, neither superuser nor BYPASSRLS,
and RLS enabled. A separate process-settings check confirmed the platform allowlist
is absent. All 21 original dirty files and original checkout revisions were preserved.
Final documentation does not change the verified application, tests or migrations.

Rollback target: staging v34 application `448a6c0c8db5bc794fbb61fb2f207912cbd5d49b`
(`registry.fly.io/pathsixsolutions-backend-staging:448a6c0c8db5bc794fbb61fb2f207912cbd5d49b`).
No schema migration, production/frontend change, new worker or capacity increase.
The earlier intermittent staging database stall remains unresolved.
