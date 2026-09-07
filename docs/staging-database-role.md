# Restricted staging database login

## Policy

Create a dedicated `pathsix_crm_staging_runtime` LOGIN role with no superuser,
createdb, createrole, replication, role inheritance or RLS bypass attributes.
Grant CONNECT and public-schema USAGE. Grant SELECT/INSERT/UPDATE/DELETE only on
active web-app data tables, with USAGE/SELECT on their serial sequences. Tenants
and role definitions are SELECT-only. No access is granted to backups,
backup_restores, migration version metadata or unused chat tables. No TRUNCATE,
DDL, ownership, grant options, or automatic grants on future tables are provided.
PUBLIC schema CREATE is revoked within the staging database so inherited public
privileges cannot undermine the boundary. The administrative role remains intact.

This is least-privilege database access, not tenant-level RLS. The runtime still
needs access to multiple tenants' records; application checks remain essential.
RLS and same-tenant foreign keys are separate follow-ups. No production role,
credential, database or deployment is changed.

## Provision and cutover

The provisioning script validates both Fly app and database host/name. It generates
a password on the staging machine, verifies actual login and effective privileges,
and writes old/new URLs to a mode-0600 temporary handoff file without printing them.
Transfer that file to a restricted local operator folder, encrypt with Windows
CurrentUser DPAPI, then delete plaintext copies before importing the new DATABASE_URL
through Fly secrets stdin. Never store the administrator URL in a second app secret.
Keep the old URL encrypted outside Git for operator recovery, and keep the existing
administrator role available for migrations/maintenance.

Before switching, run the entire regression suite with SECURITY_TEST_ROLE set to
the restricted role. Fixture setup/teardown uses the operator connection; HTTP auth
and route sessions run SET LOCAL ROLE inside disposable test schemas with the exact
same grant policy. The role-denial tests execute real SQL. CI creates a NOLOGIN
restricted test role and uses the same setup. After cutover, test setup requires an
explicit operator-supplied SECURITY_TEST_DATABASE_URL, not the limited app URL.

## Status

Local validation: 68 passed; eight PostgreSQL role tests await staging. Provisioning,
cutover and live verification results will be appended when complete.
