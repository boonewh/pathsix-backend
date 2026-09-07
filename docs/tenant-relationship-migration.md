# Same-tenant relationship constraints

Revision tenant_relationships follows tenant_membership_indexes. It adds four
unique (tenant_id, id) keys on users, clients, leads and projects, and thirty
composite foreign keys for every currently declared relationship between tables
with tenant_id. This includes record parents/source leads, assigned/creating/
updating/deleting users, file uploaders, subscription users, chat participants and
activity-log users. An inventory test checks the frozen migration against models.

Every relationship is preflighted for missing or cross-tenant parents before DDL.
Invalid records are left unchanged for review. DDL and migration history share
one transaction, with five-second lock waits and sixty-second statement limits.
Constraints are validated before commit. Existing scalar foreign keys and ORM joins
remain unchanged; the added PostgreSQL constraints are managed by Alembic rather
than duplicated as ambiguous ORM relationship paths. Creating model metadata alone
is not equivalent to applying this migration.

MATCH SIMPLE preserves nullable references. Existing required columns remain
required. The foreign keys also prevent moving a referenced parent to another
tenant while children still reference its original tenant. No data is rewritten,
no role privileges change and no additional Fly resources are required.

## Staging procedure

Use scripts/migrate_staging_membership.py --revision tenant_relationships on the
existing staging machine, supplying the operator connection through SSH stdin.
Without --apply the DDL is rehearsed and rolled back. With --apply it commits only
this revision, refusing any history other than its immediate predecessor or itself.
Existing app/database/username and 10,000-row staging size guards still apply.
An explicit reviewed migration is required to remove the protections.

## Tests and limits

Local tests run first. PostgreSQL tests exercise all thirty relationships with
cross-tenant INSERT and UPDATE attempts under the restricted role, valid and null
references, parent tenant changes, invalid-data preflight, idempotence and atomic
rollback. Grants for otherwise hidden tables exist only inside disposable test
schemas. A separate HTTP test applies the constraints before creating a client,
account, contact, project and interaction through normal routes.

This does not enforce row-level read isolation. Polymorphic activity entity IDs,
parent-cardinality rules, remaining service migration and transaction-local RLS
remain separate work. Production remains untouched and unauthorized for deployment.

Staging verification pending.
