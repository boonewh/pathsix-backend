# Read-only MCP client pilot

The `/mcp` resource uses the official MCP Python SDK 2.2.0 with stateless
Streamable HTTP and JSON responses. It supports the legacy initialization
handshake and the 2026-07-28 discovery protocol. Authorization remains the
operator-registered [OAuth pilot](oauth-browser-flow.md); no real client has been
enrolled. Protected-resource metadata is served at
`/.well-known/oauth-protected-resource/mcp`.

## Tools and data

Both tools require `clients:read` and use the same current user roles, company
boundary, assignment/creation rules and deleted-record filter as ClientService.
`list_clients` returns up to 50 summaries (default 20), ordered by ID, with an
`after_id` cursor. `get_client` returns one accessible summary or the same error
for an absent or inaccessible record. Fields are limited to ID, name, city, state
and type, with explicit database-side text bounds. Notes, contact details,
addresses, related records and web view-activity mutations are excluded.
Tool descriptions identify CRM text as untrusted data, never instructions.

The adapter accepts a raw access token, not a caller-supplied identity, owner,
role or scope. Every request reloads current membership, client configuration,
consent, credential expiry and resource binding. Each tool call checks its own
scope and revalidates after taking the grant lock shared with revocation.
Web login JWTs, authorization codes and refresh tokens cannot authenticate MCP.
There are no write tools, file access, prompts or server-side sessions.

## Transport and audit boundaries

Only POST JSON requests are accepted. Host must match the configured HTTPS
resource; an Origin, if present, must exactly match the issuer. Forwarded headers
do not establish either value. Query strings, session IDs, duplicate security
headers, JSON batches, duplicate JSON keys and non-finite numbers are rejected.
Bodies are limited to 8192 bytes and ten seconds. Responses prevent caching.
The SDK's payload logging is suppressed. Server-to-server clients are the initial
target; cross-origin browser access is not enabled.

Successful and denied authenticated tool invocations record only owner/grant,
an allowlisted tool label, outcome, result count and time. Raw arguments,
CRM values and credentials are never persisted in this audit. A successful read
commits its audit and last-used timestamp before returning data; audit failures
release no CRM data. The runtime may insert and read owner-scoped audit rows but
cannot update, delete or truncate them. A durable limit of 60 calls per grant per
minute is serialized by the grant lock. Limited requests do not grow the audit;
transport/authentication failures are also outside this tool audit.

`mcp_read_audit` follows `oauth_browser_flow`. This additive migration creates
`ai_tool_audits`, with a composite foreign key to its owner grant and two forced
RLS policies. The complete inventory becomes 21 application tables, 17 protected
tables and 24 policies. Migration rehearsal rolls back. Application rollback is
staging v38 / `7b99deb`; retain audit history and the additive schema on rollback.

## Verification and remaining gates

Tests cover both protocol eras, official SDK client interoperability, simultaneous
identities, revoked/current permissions, same-company and cross-company denials,
strict arguments, bounded pagination, malformed transport, audit failure,
append-only ownership, concurrent call limits and transactional migration errors.
The existing CRM regression and complete isolation-inventory tests also run.
[PostgreSQL 18/RLS CI](https://github.com/boonewh/pathsix-backend/actions/runs/36655111530)
passed **585 tests with zero skips**. Local verification passed 511 checks across
the full run and the final transport addition; 74 PostgreSQL-only cases were
covered by CI. The staging migration rehearsal rolled back cleanly, and apply
advanced only the schema revision while preserving full-row fingerprints for
all 20 pre-existing application tables.

The clean archive of `2b31aed16ac2451b1070e1d69e2985beed458d3f` runs as
**staging v39**, image digest
`sha256:929be663730283fc4ae75b1f827e8b3ac2ef9e01d3ac17e9b1aedf4e0aefabd0`.
All **190 deployed OAuth/MCP/isolation/migration checks passed** using paced,
disposable schemas over the staging primary's private endpoint. All 21 public
application tables retained identical full-row fingerprints, no test schemas
remained, and the restricted-runtime/17-table/24-policy attestation matched before
and after. The real client catalog, grants, credentials and audit table remain
empty. Public CRM identity/configuration, OAuth pages and denial paths, MCP
metadata, authentication challenges and foreign-origin rejection also passed.
Both existing machines passed health checks with unchanged capacity, services
and mounts. All 21 original dirty files and all three checkout revisions remain
preserved. The SSH wrapper emitted its known Windows handle warning after the
complete successful test and snapshot output; the saved JSON independently
confirms zero test failures and unchanged data. Later documentation commits do
not change the deployed application. Production, frontend and platform workers
were unchanged.

This is a narrow staging pilot, not a production launch. Real client enrollment
and an end-to-end test in the chosen AI product, distributed unauthenticated abuse
controls, operational monitoring, audit retention policy and the existing staging
database connection/resource-pressure investigation remain open. Dynamic client
registration, broader origins/callbacks and all write tools remain separate work.
