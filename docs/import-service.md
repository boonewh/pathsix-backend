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
import coverage also passes. PostgreSQL/RLS CI and staging verification are pending.
Positive staging checks will use disposable schemas and mocked email delivery;
the shared public tenant must retain its existing users/leads and permissions.

The current rollback target is staging v32 / `2149bb6`:
`registry.fly.io/pathsixsolutions-backend-staging:2149bb6e5e960ff5a91093c864dc710872f22e6e`.
No schema migration, frontend change, production deployment or resource increase
is part of this increment. The earlier intermittent staging database stall remains
unresolved. Import idempotency, durable notification delivery and delegated grants
are still prerequisites before exposing imports as MCP writes.
