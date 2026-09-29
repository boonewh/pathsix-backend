# OAuth browser flow and connection management

This increment adds a backend-served sign-in/approval page, connection management,
authorization-server discovery and Authlib 1.8 authorization-code/PKCE exchanges.
It remains an operator-registered pilot: no client is automatically enrolled and
there is no MCP resource endpoint or tool yet.

## Public endpoints

| Endpoint | Contract |
| --- | --- |
| `GET /.well-known/oauth-authorization-server` | Configured issuer, working authorization/token/revocation endpoints, code flow, S256 and public-client authentication. No dynamic registration advertised. |
| `GET /oauth/authorize` | Exact registered HTTPS redirect, explicit resource, read scopes, state and PKCE S256 required. Invalid requests never redirect. |
| `POST /oauth/decision` | CRM bearer authentication, exact issuer Origin, short signed intent, secure browser-binding cookie and explicit boolean decision. Returns only the registered callback with code or denial, state and issuer. |
| `POST /oauth/token` | Form-encoded authorization-code or refresh exchange. Explicit client/resource required; code also requires exact redirect and verifier. No client secrets, web JWTs, query parameters, duplicates or JSON token requests. |
| `POST /oauth/revoke` | Form-encoded `client_id` and `token`, optional hint. Matching access or refresh credential revokes the whole grant; unknown token/client combinations return the same empty success. |
| `GET /oauth/connections` | Mobile-friendly sign-in, bounded history pagination and owner-only revocation through the existing consent API. |

The browser retains the CRM token only in memory. It displays the signed-in email
and company after a fresh permission preview, before the separate approval action.
No credential goes into browser storage or an AI callback. Pages use only local
assets, a restrictive CSP, no framing, no referrer and no-store responses. OAuth
errors and token responses also prevent caching. Authorization responses include
`iss` to identify the authorization server.

## Credential lifecycle

Codes expire after two minutes and can be exchanged once with the S256 verifier.
Access tokens last at most five minutes; refresh tokens last at most seven days.
Every token is an opaque random 256-bit value, stored only as a SHA-256 digest,
bound to an owner grant, registered client, configured resource and exact scopes.
No token can outlive its original 30-day consent. Refresh rotates both credentials
and may reduce scopes; it cannot add scopes or extend consent.

Grant-first row locking serializes concurrent exchanges and revocations. Reusing
a consumed code (with the correct verifier) or refresh token revokes the entire
grant, including newly issued credentials. Invalid exchanges commit this replay
revocation but never return new credentials. Grant revocation, inactive user or
company, disabled client/OAuth configuration and removal of current permissions
are checked on the next evaluation. The resource validator returns a distinct
`DelegatedIdentity`; existing web services reject it as a web Principal.

The earlier per-owner consent limits remain. Token issuance additionally has a
transactional limit of 20 exchanges per grant per minute. Endpoint IP limits are
process-local and conservative behind the shared proxy; distributed abuse limits
and operational monitoring remain required before a broad production rollout.

## Database and rollout contract

`oauth_browser_flow` follows `ai_consent_grants`. It adds disabled-by-default OAuth
configuration to the operator-owned catalog, a composite grant-owner key, and
`oauth_credentials`. The runtime cannot change the catalog, delete credentials,
rewrite their owner/permissions/expiry or reset consumed credentials. The new
table requires both transaction-local user and company under forced RLS.

The only credential bootstrap is a fixed-search-path SECURITY DEFINER function
returning user/company IDs for a presented credential digest. PUBLIC cannot call
it. A separate fixed-identity session reloads active membership and roles, then
closes before the owner session starts. Bootstrap does not expose credentials,
grant records or business data. The complete inventory is 20 application tables,
16 protected tables and 22 reviewed policies.

Migration is additive and transactional, with staging-only rehearsal/apply support.
Application rollback target is staging v37 / `3770a2d`; retain new tables and
revocation history on rollback. Never downgrade by erasing credentials.

## Validation

Coverage includes browser binding, explicit/expired/replayed consent, unsafe and
duplicate parameters, PKCE, resource/client/redirect mismatch, expiry, rotation,
scope reduction, replay-family revocation, current membership/role/client changes,
cross-owner isolation, immutable history, concurrent exchange and real migration
rollback. Existing CRM regression and complete table-policy fault tests also run.
Release-specific CI, browser and deployed verification evidence is recorded below
after completion.

## Remaining MCP work

The next increment is a narrow read-only MCP adapter with token authentication,
tool-specific scope checks, the existing record-level service rules, bounded
results, invocation auditing and adversarial delegated-access tests. Do not
advertise protected-resource metadata until that resource works. Real client
registration, dynamic client metadata/SSRF protection, broader callback support,
production rollout and all write tools remain separate work.

Protocol references: [Authlib authorization server](https://docs.authlib.org/en/stable/oauth2/authorization-server/index.html),
[OAuth security best practices](https://www.rfc-editor.org/rfc/rfc9700.html), and
[MCP authorization](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/basic/authorization/index.mdx).
