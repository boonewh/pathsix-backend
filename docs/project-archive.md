# Private ASFI Project archive

The direct frontend address is `/owner/project-archive`. Its API lives at
`/api/owner/project-archive`. Neither an admin role nor a guessed URL grants access.
`PROJECT_ARCHIVE_OWNER_IDS` must contain the verified numeric IDs for the active
owner accounts in each environment. Production owners are Will (2) and the PathSix
admin account (36). Empty or invalid configuration denies everyone. Ordinary
admins cannot edit those identities or use whole-database backup endpoints.

The archive is fixed to tenant 1 and Projects created before `2026-05-01T00:00:00Z`.
Preview includes Projects already in Deletes and does not change records. A move
requires a signed preview (15-minute expiry), the same owner, unchanged Project
contents, and the exact typed count. Deploying this feature does not move records.

Projects remain in the database with archive metadata. Normal ORM queries hide
them, associated interactions, and their activity events, including aggregates,
searches and bulk operations. A write guard also stops stale loaded objects from
altering archived Projects or interactions. Foreign keys preserve their links.
The private page allows reading and restoring, with an append-only action history;
restoration preserves the prior Deletes state. Leads and Accounts are never moved.

## Deployment

1. Apply `scripts/add_project_archive_columns.py` with the environment's database
   administrator connection before deploying application code. It is additive,
   idempotent, transaction-bound and does not update business rows. It fails on
   lock contention rather than waiting indefinitely. Existing Alembic histories
   are left intact; this script must also be applied to separately migrated DBs.
2. Verify owner emails and IDs in that database, then configure
   `PROJECT_ARCHIVE_OWNER_IDS` on the server. Never derive these IDs from HTTP input.
3. Deploy backend, then frontend, to both main and staging. Preserve staging's RLS
   and service layer. Only an authenticated private archive request adopts tenant
   1; the owners' normal tenant access is unchanged.
4. Verify both owners can read the empty archive/preview and other users get 403.
   Check normal CRM reads and confirm the archive count is still zero.

Do not roll back to code without archive filtering after any records have been
archived: that would make them visible again. Keep the filtering in a rollback or
restore the records deliberately first. Never drop the archive columns while the
application depends on them.

## Checks

`tests/test_project_archive.py` exercises real HTTP authorization, the date and
tenant boundaries, preview validation, reporting/search visibility, restore,
owner-account protection, and stale/bulk writes. Staging CI also runs those paths
with PostgreSQL row security and its restricted runtime role. Frontend
`tests/project-archive.spec.ts` checks access denial, preview without writes, typed
confirmation, restoration, and mobile width.
