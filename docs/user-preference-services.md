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
PostgreSQL/RLS CI and staging verification results will be recorded here before
the staging merge. Positive account/preference writes and password-reset tests
use disposable staging schemas with email delivery mocked. Public staging users,
roles and preferences must remain unchanged.

Rollback target: staging v33 application `dfd8230a449f9ea5f1e4e01944d9fa67e428f1ec`
(`registry.fly.io/pathsixsolutions-backend-staging:dfd8230a449f9ea5f1e4e01944d9fa67e428f1ec`).
No schema migration, frontend/production deployment or resource increase is required.
Background identity review, delegated consent/scopes and MCP tooling remain separate
work. The earlier intermittent staging database stall is still unresolved.
