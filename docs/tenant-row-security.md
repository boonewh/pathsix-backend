# Tenant row security

The PostgreSQL runtime role gets row-level policies on fourteen tables: the ten
business tables with tenant_id, users, tenants, user_roles and user_preferences.
Global role definitions remain global; backup/platform tables remain inaccessible
to the runtime role through table privileges. Policies cover SELECT and mutations,
so forgotten SQL predicates cannot expose or change a different tenant's records.

Every SQLAlchemy transaction sets all identity settings with set_config(..., true).
Absent identity becomes an empty setting and matches no business rows. Commit and
rollback clear settings before a pooled connection is reused. HTTP context comes
from the immutable authenticated principal, never request-supplied tenant IDs.
Standalone services bind a principal to their session; a session cannot switch
identity, including after commit. Legacy ORM tenant filters remain in place.

## Authentication bootstrap

Login and forgot-password perform an exact email lookup. Protected requests use
the subject of a verified JWT. A narrowly scoped SECURITY DEFINER function returns
only user/tenant IDs for exactly one identifier. It uses qualified table names,
search_path=pg_catalog, no dynamic SQL, and grants EXECUTE only to the runtime role.
Its operator owner bypasses RLS only for this lookup. Bootstrap policies reveal
only that user, its tenant configuration and role memberships, with no business
records or preferences. The application still verifies passwords, active user/
tenant state and current roles before creating an authenticated principal.

Only after validating a signed reset token does a reset session receive its exact
user update context. Normal login/forgot-password contexts cannot update users.
Existing password-reset delivery behavior and production Resend setup are preserved.

RLS trusts the server to establish identity. It protects against omitted filters
and context leakage; it is not a sandbox against arbitrary SQL executed with stolen
runtime credentials or malicious server code that can set its own settings.
Same-tenant ownership/assignment permissions still belong to application services.

## Deployment sequence (staging only)

1. Deploy application support with CRM_RLS_ENABLED unset. Test disposable schemas
   with CRM_RLS_ENABLED=1 and SECURITY_TEST_ROLE set to the restricted role.
2. Rehearse and apply tenant_rls_prepare through the guarded staging runner. This
   prepares policies and the identity lookup without activating row security.
3. Set CRM_RLS_ENABLED=1 on the staging backend and verify login before enforcement.
4. Rehearse and apply tenant_row_security. It requires the exact policy inventory
   and application flag, then enables and forces RLS on all fourteen tables.
5. Independently verify policy state, live CRM operations and test cleanup.

Both phases use the existing operator URL via SSH stdin, never a persistent
administrator app secret. No added machines, resizing or production deployment.
Do not disable the application flag after activating RLS: unauthenticated database
reads will correctly stop working. Any rollback of policies requires a separately
reviewed operator action; an old application image alone is not compatible.

References: PostgreSQL row security and SECURITY DEFINER guidance:
https://www.postgresql.org/docs/current/ddl-rowsecurity.html
https://www.postgresql.org/docs/current/sql-createfunction.html

## Validation

Local: 73 existing tests passed; new real login/reset test also passed. PostgreSQL
verification: **98 passed in 67.30 seconds**, with RLS enabled for every PostgreSQL
HTTP fixture and the dedicated raw-SQL/pool/bootstrap tests.

Staging **v18**, application commit **000de69920a2daaea8e6284967b2e9bc99de5145**,
has CRM_RLS_ENABLED=1 and Alembic head tenant_row_security. Both preparation and
activation rehearsals/commits passed. Live login succeeded before and after
activation; thirteen protected read endpoints and the full client lifecycle passed
without page errors. A separate operator connection verified all fourteen tables
have ENABLE and FORCE RLS; the bootstrap function has no PUBLIC execute grant.
The actual runtime connection without context returned zero clients, leads, users
and tenants. Original counts remain two clients/two leads, and zero temporary
test schemas remain. Production, frontend deployment and machine sizes/count are
unchanged.

Do not roll back to the pre-RLS application or unset CRM_RLS_ENABLED while policies
are active. Operator scripts must use operator credentials or explicit session
identity as appropriate; a raw runtime connection now intentionally sees no rows.
Polymorphic relationship constraints, parent-cardinality constraints, remaining
service extraction and delegated OAuth/MCP remain separate work.
