# Tenant-bound lead imports — September 29, 2026

ImportService now owns preview/template generation, CSV/XLSX parsing, mapping
validation, active same-tenant assignee lookup, row orchestration and summary-email
preparation. Its constructor requires a trusted admin Principal, including calls
outside HTTP. The routes only adapt multipart requests, commit and deliver email.
Lead creation still goes through LeadService and its tenant option rules/audit hooks.

## Preserved behavior and corrections

- The three `/api/import/leads/{preview,submit,template}` endpoints and response
  fields are preserved. Preview returns ten sample rows and the full row count;
  submission reports successful/failed counts, row failures and warnings.
- Individual validation/constraint failures roll back their own savepoint and
  activity history while later valid rows continue. Infrastructure/programming
  failures abort the batch and propagate to a safe HTTP error instead of exposing
  SQL/provider details or claiming partial success after a connection failure.
- The service never commits or sends email. The adapter commits successful rows
  once, then delivers at most one summary to the validated assignee. All-invalid
  batches, validation errors, outages and failed commits send no summary. Email
  failure after commit does not undo a successful import.
- Caller rollback removes every imported row and its audit records, including
  SQLite's legacy transaction mode: a real outer BEGIN precedes row savepoints.
- Only explicit lead fields can be mapped. Unknown, duplicate, malformed or
  missing mappings fail before writes. `tenant_id`, assignment and audit identity
  can never come from uploaded columns. Foreign/inactive/missing assignees share
  the same error. Malformed JSON mappings now return 400 instead of 500.
- CSV preserves leading-zero ZIP codes and literal text such as `NA`; UTF-8 BOM,
  Windows-1252 and Latin-1 decoding use the same original bytes. Phone labels are
  canonicalized case-insensitively, invalid phone values warn and are omitted,
  emails are lowercased, and tenant status/type normalization remains in LeadService.
- Parsing is bounded to 20 MiB input, 10,000 data rows and 200 columns. XLSX is
  read-only/data-only with external-link preservation disabled, at most 1,000 ZIP
  entries and 100 MiB declared expanded content. These are backend limits, not
  an MCP import tool contract. Duplicate/empty headers and extra row columns fail
  explicitly rather than silently shifting or overwriting data.

## Verification and rollout

Full local suite: **295 passed, 29 PostgreSQL-only skipped**. The 25 new import
cases cover direct/HTTP authorization, mappings, tenant options, transaction/audit
rollback, row constraints, outages, commit/email failures, CSV text/encoding, XLSX,
parser bounds, template compatibility and bounded samples. Existing tenant-option
import coverage also passes. [PostgreSQL 18/RLS CI](https://github.com/boonewh/pathsix-backend/actions/runs/36599073826)
passed **324 tests with zero skips**. All **25 focused import tests passed** against
the deployed application and PostgreSQL in disposable schemas. Positive import
tests mocked email delivery. Public counts remained 2 leads, 67 activities and
1 user, with no test schemas remaining. The runtime role remained
`pathsix_crm_staging_runtime`, with neither superuser nor RLS bypass privileges
and `CRM_RLS_ENABLED=1`.

Live HTTP checks passed for CSV/XLSX preview, text preservation, the template,
malformed mappings, a missing assignee and an all-invalid batch. User roles were
unchanged. The first disposable test transfer had a Windows text-encoding error;
explicit UTF-8 transfer corrected it before the complete successful rerun.

Staging **v33** runs application revision
`dfd8230a449f9ea5f1e4e01944d9fa67e428f1ec`, built from a clean Git archive, with image
digest `sha256:81d0ad56dc5ba91cd11db2d05259d761902e304fd9f450cddd8dd0fa53af9dea`.
Both machines passed health checks. Fly replaced machine `7812092ae397d8` with
`185777d6c554e8`; `82549ec7079218` remained. The two-machine count, 1 GiB VM sizes,
services and lack of persistent mounts were unchanged. All 21 preexisting dirty
files and the original backend/frontend checkout revisions were preserved.

The rollback target is staging v32 / `2149bb6`:
`registry.fly.io/pathsixsolutions-backend-staging:2149bb6e5e960ff5a91093c864dc710872f22e6e`.
No schema migration, frontend change, production deployment or resource increase
is part of this increment. The earlier intermittent staging database stall remains
unresolved. Import idempotency, durable notification delivery and delegated grants
are still prerequisites before exposing imports as MCP writes.
