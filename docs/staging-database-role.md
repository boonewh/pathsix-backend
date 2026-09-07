# Restricted staging database login

## Policy

Create a dedicated `pathsix_crm_staging_runtime` LOGIN role with no superuser,
createdb, createrole, replication, role inheritance or RLS bypass attributes.
Grant CONNECT and public-schema USAGE. Grant SELECT/INSERT/UPDATE/DELETE only on
active web-app data tables, with USAGE/SELECT on their serial sequences. Tenants
and role definitions are SELECT-only. No access is granted to backups,
backup_restores, migration version metadata or unused chat tables. No TRUNCATE,
permanent schema DDL, ownership, grant options, or automatic grants on future tables are provided.
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

## Operator recovery

Credentials are stored outside Git under the operator's LocalAppData
PathSix/staging-database-access folder with restricted ACLs and CurrentUser DPAPI.
Both plaintext handoff copies were deleted. From this checkout, the operator can
run scripts/set_staging_database_login.ps1 -Mode Runtime for the limited login,
or -Mode Administrator for an emergency staging rollback. The helper validates
the exact staging host, database and username and sends the URL through stdin.
Neither command targets production. Administrative migrations/tests must use
an operator connection temporarily, never an additional administrator app secret.

## Status

The initial 76-test PostgreSQL suite passed, with HTTP auth/routes using the
restricted role and fixture setup/cleanup using the operator connection.
Staging release v12 switched DATABASE_URL to pathsix_crm_staging_runtime.
Both machines independently confirmed this current_user with superuser,
createdb, createrole and RLS-bypass flags false.

The post-cutover live lifecycle revealed that client purge unnecessarily loaded
the unused chat relationship, and rollback expired the ORM user later accessed
by request logging. The follow-up makes client chat deletion passive (the FK
continues protecting existing references) and logs immutable principal IDs.
It does not grant the application access to chat tables or change schema.
Local validation after these fixes: 70 passed, eight PostgreSQL-only tests skipped.
Final staging release **v13**, application commit **e311c19**, passed all **78
tests in 39.68 seconds** directly on the staging machine. HTTP auth/routes used
the restricted role; fixture setup/cleanup used an operator URL supplied through
stdin, with no persistent administrator app secret. Live browser login, search,
reports, clients page and full client create/read/update/delete/restore/purge
passed without page errors. Both test clients were removed; public counts are
two clients and two leads. Zero disposable schemas remain.

The first expanded run through a local Fly tunnel had 66 passes and 12 connection
setup errors; the complete direct-machine rerun above supersedes it. A psycopg2
traceback from that failed run exposed the old administrator password in tool
output. That staging-only administrator password was rotated immediately, the
new login verified and its DPAPI recovery copy updated. The separate runtime
credential was unchanged. The staging test helper now disables tracebacks;
operator invocations must also use --tb=no to avoid driver argument disclosure.
One schema from an interrupted tunnel rerun was removed after tests finished.

Production has not been deployed or reconfigured. This milestone removes
application superuser access; it does not complete tenant RLS, composite foreign
keys, index drift reconciliation or the remaining tenant-service migration.
