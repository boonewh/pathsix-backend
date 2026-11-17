# Tenant-bound users and personal preferences — September 29, 2026

UserService owns admin user lists, creation, email/role updates, activation and
password-reset recipient lookup. PreferenceService owns default merging and
pagination preferences for the authenticated user. Both take a trusted per-operation
Principal and work outside HTTP; neither commits nor delivers email.

## Behavior and boundaries

- UserService requires admin authorization at construction. All target lookups
  explicitly scope the tenant. Foreign and absent IDs return the same not-found
  response. Creation derives tenant identity from the Principal, and self-deactivation
  remains forbidden. Responses expose neither hashes nor tenant internals.
- Role selections must be a list of known role names. A mixed valid/unknown list
  now fails as a whole instead of silently granting its recognized subset. Empty
  lists retain the existing ability to remove roles. This change does not introduce
  a last-admin policy or delegated role-management grants.
- Email uniqueness remains global. Visible duplicates and database uniqueness
  collisions with hidden/concurrent accounts produce the same safe validation error,
  without querying another tenant or exposing SQL. Email case behavior is unchanged.
  Password creation rejects non-text, empty and over-72-byte values instead of relying
  on bcrypt truncation/errors. No new password-strength policy is introduced.
- Preferences have no tenant column: the service joins their owner and explicitly
  requires both the Principal's user and tenant. Admin access does not permit reading
  another user's preferences. Inactive/mismatched owners are denied. New rows always
  use the Principal's user ID, ignoring uploaded identity fields.
- Existing defaults, partial-default merging, allowed tables and invalid-view fallback
  are preserved. Returned defaults are independent deep copies. Boolean page sizes
  are rejected. A competing preference insert returns a safe conflict response.
- Routes preserve paths and response shapes, own commit/rollback, and mark mutations
  before execution so a connection failure is not advertised as safe to retry.
  User/preference reads send `Cache-Control: no-store`. Password-reset lookup releases
  its database connection before delivery; SMTP failures return a safe 503.

## Verification and rollout

Focused tests cover direct and HTTP authorization, same/foreign tenant isolation,
identity forgery, caller rollback, complete role validation, hidden email collisions,
current-role revocation, malformed inputs, personal preferences, independent defaults,
commit/disconnect/conflict failures and password-reset delivery. Database-backed reset
tests replace route-query mocks. PostgreSQL fixtures now also bind preference routes
to their isolated test schema and restricted runtime role.

The full local suite passed **330 tests**, with **29 PostgreSQL-only skips**.
[PostgreSQL 18/RLS CI](https://github.com/boonewh/pathsix-backend/actions/runs/36606247316)
passed **359 tests with zero skips**. All **40 focused checks** (35 user/preference
cases and five password-reset cases) passed against deployed staging code in disposable
PostgreSQL schemas. Positive account/preference writes and password-reset tests mocked
email delivery. The runtime role was `pathsix_crm_staging_runtime`, with neither
superuser nor RLS bypass privileges and `CRM_RLS_ENABLED=1`.

Before/after public snapshots matched: one user, one role membership, zero preferences,
two leads and 67 activities. A digest of all user/membership/preference rows was
unchanged, and no test schemas remained. Live HTTP reads and rejected self-deactivation,
mixed roles, invalid email bodies/preferences and missing reset recipients also passed;
the public users/roles/preferences response fingerprint was unchanged.

Application revision `448a6c0c8db5bc794fbb61fb2f207912cbd5d49b`, built from a clean Git
archive, runs as **staging v34**. Image digest:
`sha256:b9dd9ed983b1f34f0bb0c4ea44fc539c283599c2f93f90a216c277634c1ea6c7`.
Both existing machines (`82549ec7079218`, `185777d6c554e8`) passed health checks with
unchanged 1 GiB capacity, services and mounts. All 21 preexisting dirty files and
the original backend/frontend checkout revisions were preserved. Final documentation
does not change the verified application, tests or migrations.

Rollback target: staging v33 application `dfd8230a449f9ea5f1e4e01944d9fa67e428f1ec`
(`registry.fly.io/pathsixsolutions-backend-staging:dfd8230a449f9ea5f1e4e01944d9fa67e428f1ec`).
No schema migration, frontend/production deployment or resource increase is required.
Background identity review, delegated consent/scopes and MCP tooling remain separate
work. The earlier intermittent staging database stall is still unresolved.
