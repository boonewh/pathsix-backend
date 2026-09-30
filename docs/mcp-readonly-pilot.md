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
Release evidence is recorded after staging verification.

This is a narrow staging pilot, not a production launch. Real client enrollment
and an end-to-end test in the chosen AI product, distributed unauthenticated abuse
controls, operational monitoring, audit retention policy and the existing staging
database connection/resource-pressure investigation remain open. Dynamic client
registration, broader origins/callbacks and all write tools remain separate work.
