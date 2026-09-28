# Tenant-bound file storage — September 28, 2026

StorageService now owns file listing, upload, download and deletion. HTTP routes
only construct trusted context, decode multipart requests and encode responses.
This is foundation work; it exposes no MCP tools or delegated AI grants.

## Authorization and paths

- All authenticated tenant members may list/download tenant files, preserving the
  existing shared-file policy. Upload/delete require the current `file_uploads`
  role, including direct service calls. Admin alone does not imply that role.
- File IDs and uploader relationships are explicitly tenant-scoped without HTTP
  filters or RLS. Foreign and missing IDs return the same 404 before storage I/O.
- Object keys must exactly match `tenant-{principal.tenant_id}/{stored_name}`.
  A same-tenant metadata row pointing at another tenant's object is rejected.
- Legacy local pointers remain readable only beneath the configured storage root,
  in `tenant-N` or historical `N` directories, with the matching stored filename.
  Arbitrary absolute paths and paths into another tenant are no longer accepted.
- Local storage rejects traversal, Windows absolute/alternate-stream forms and
  symlink/junction redirection. Filesystem ownership must still prevent untrusted
  processes from racing path validation. Reads no longer create directories.
- New stored names are UUIDs with a short alphanumeric extension. Original names
  are metadata only; download attachment headers use framework escaping.
- Uploads validate the whole batch before writing and enforce the aggregate size
  limit in the service as well as the HTTP request limit. Provider error details
  are not returned to users. Missing objects return 404; provider outages return 503.

## Transaction ownership and limits

Unlike database-only services, storage mutations own a fresh, dedicated session's
commit/rollback because they must coordinate external objects. An already active
transaction is rejected rather than committed implicitly. Reads remain pure.
Ordinary upload failures roll back metadata and compensate newly written objects.
Delete failures retain metadata; a database failure after deleting bytes attempts
to restore the original bytes. Missing objects can have stale metadata deleted.

These are compensating operations, **not distributed atomic transactions**. Process
termination, concurrent operations around rollback, ambiguous commits and failed
compensation still need durable reconciliation before MCP writes. An upload commit
whose acknowledgement is lost retains its objects rather than potentially deleting
committed data. Safe error logs identify compensation/unknown-commit incidents
without exposing provider messages. No durable outbox or reconciler is added here.

Staging currently uses local storage on two machines without mounted volumes and
has zero uploaded files. Local disk is not shared or durable across replacement;
durable shared object storage is an operational prerequisite for a real file pilot.
This increment does not change vendors, provision storage or increase resources.
Existing unbounded file listing/download behavior must also be bounded for MCP.

## Validation

Local suite: **270 passed, 29 PostgreSQL-only skipped**. The 25 new storage cases
cover direct/HTTP two-tenant reads and denials, current-role revocation, malformed
pointers and uploader links, local/object-backed round trips, multipart Unicode
attachment handling, batch validation, write/commit failure compensation, ambiguous
commit acknowledgement, failed-compensation logging, fresh-session enforcement,
legacy paths, symlink isolation and S3 missing-object/stream handling.

The first PostgreSQL/RLS run passed 297 tests and exposed two alert-capture failures:
Alembic disabled existing application loggers during in-process migration tests.
Migration logging now preserves existing loggers; all 25 focused cases pass locally.
Corrected [PostgreSQL 18/RLS CI](https://github.com/boonewh/pathsix-backend/actions/runs/36487906519)
passed **299 tests with zero skips**. S3 behavior is
tested with an in-memory object backend and adapter doubles, not a live S3 bucket.

## Verified staging rollout

[PR #17](https://github.com/boonewh/pathsix-backend/pull/17) application revision
`2149bb6e5e960ff5a91093c864dc710872f22e6e` is deployed as **staging v32**.
Both existing machines, `82549ec7079218` and `7812092ae397d8`, have that revision
and passing health checks. VM size, HTTP services, sleep behavior and mount
configuration match the saved predeployment baseline. Image digest:
`sha256:f667ab8c8b97c606191d764041c002e5b1b17bb96af6a3b0b02078be975642da`.

The 25 focused storage checks also passed against the deployed application in
19 seconds, using disposable PostgreSQL schemas and the restricted runtime role.
This includes local-file and object-double round trips, two-tenant boundaries,
HTTP multipart/download handling and failure recovery. Before/after counts were
unchanged: **zero public files, one public user, zero test schemas**. Runtime
inspection confirmed `pathsix_crm_staging_runtime`, `rolsuper=false`,
`rolbypassrls=false` and `CRM_RLS_ENABLED=1`.

Live public-API login, six health/protected reads and four storage checks passed:
empty listing, missing download, and upload/delete denial for the existing admin
without `file_uploads`. No public role, user or file was created or modified.
The positive upload/delete HTTP tests used isolated schemas on the staging
machine, not its shared public tenant. S3 remains unverified against a live bucket.

All 21 originally changed files and all three original checkout HEADs were
verified unchanged. Production and frontend deployment remain unchanged. Safe
machine evidence, the exact source archive and isolated verification scripts/results
are under `temp/storage-service-rollout/`. The earlier intermittent staging DB
stall remains unresolved; this run does not establish its cause or resolution.

Rollback image:
`registry.fly.io/pathsixsolutions-backend-staging:30c6ab2476648535c5922e7da40a70e6a7ffa525`.
The final handoff commit changes documentation only; v32's application source is
still the tested `2149bb6` revision.
