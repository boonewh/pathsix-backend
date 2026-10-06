# Reviewed lead creation through ChatGPT

This is the first write increment in the locked MVP. ChatGPT can find leads,
read a selected lead and current option labels, and prepare a proposed lead.
The connected user opens the returned review link, signs in to the same PathSix
account, and confirms or cancels the exact saved proposal. Only confirmation
creates the CRM record. ChatGPT checks the action receipt afterward.

## Permissions and tools

`list_leads`, `get_lead` and `get_lead_options` require `leads:read`.
`prepare_lead_creation` and `get_lead_creation` require both `leads:read` and
`leads:create`. Existing `clients:read` connections still expose only their two
client tools. The operator client catalog must explicitly allow new permissions;
existing grants and credentials are never widened. A new consent is required.

Creation reuses LeadService and its field/default normalization. The new lead is
owned by the connected user in their current tenant, without assigning another
user or sending email. Assignment remains a later MVP increment. Unknown fields,
owner/tenant overrides, model-supplied approval and excessive input are rejected.
Read results and proposal text are untrusted data, rendered as text in the review.

## Confirmation and recovery

MCP can prepare and check an action but has no confirmation tool. The review API
requires a normal CRM login, the grant's exact owner, current membership and
permissions, an enabled OAuth client, same-origin requests, a secure browser
cookie and a short signed review bound to the owner, action and payload hash.
An action identifier or delegated token alone cannot approve it.

Proposals expire after ten minutes; signed browser reviews expire after five.
Cancellation is terminal. A changed proposal needs a new request key and review.
An unchanged retry uses the original 16–64-character request key. The original
normalized input fingerprint prevents a key being reused for different data.
At most 100 new proposals per grant per day are accepted; existing durable tool
call limits also apply. No new permission is granted by a request key or receipt.

Grant locks serialize preparation, confirmation and revocation. Confirmation locks
the action after its grant. Lead insertion, the terminal receipt and audit commit
together. A concurrent or repeated confirmation returns the same result. On an
ambiguous network response, the page disables further confirmation and offers
Refresh status; it never automatically resubmits the write. Both browser and MCP
receipt reads recheck the grant and current access to the resulting lead.

## Database and rollout

The additive `mcp_lead_creation` revision follows `mcp_read_audit`. It creates
`ai_write_actions` with an owner/grant composite foreign key, a unique per-grant
request key, forced owner RLS and immutable proposal/terminal-state protection.
The runtime can select/insert and update only status, result ID and decision time;
it cannot delete receipts or rewrite input. The isolation inventory becomes 22
application tables, 18 protected tables and 25 policies.

The stored proposal contains the reviewed CRM fields. Audits contain only the
allowlisted action/tool, owner, outcome, count and timestamp. Do not include
proposals, authentication tokens or SQL parameters in logs. Receipt/proposal
retention and purge policies remain a bounded production-readiness requirement
in the MVP plan; no cleanup job is enabled by this increment.

Before staging rollout, complete PostgreSQL CI, rehearse and apply the guarded
migration, verify the policy/privilege inventory and deploy the exact tested
revision. Enable the new scopes only for the intended staging OAuth client and
obtain fresh consent. Preserve existing grants. Verify synthetic creation,
cancellation and response-loss recovery, unchanged unrelated data and test-schema
cleanup. The real ChatGPT phone workflow must pass before this milestone is
marked complete. No customer record is a smoke-test target.

Rollback retains the additive table and all receipts. Roll back only to the
previous staging code that also includes the project-archive filtering; never
use an older pre-archive image. Revoking a grant blocks further actions but does
not undo a lead already created. No schema downgrade erases action history.

## Verification status

Local focused verification passed 85 tests with 5 PostgreSQL-only skips after
integration of staging's project-archive change. Thirteen browser checks passed
at phone and desktop widths, including escaped proposal text, wrong-account
denial, cancellation, account switching, receipt recovery after reload and a lost
response after commit. Browser credentials remain in memory only. Evidence and
the local synthetic browser harness are in `temp/mvp-lead-creation` in the outer
workspace.

The first full local run passed 555 tests, skipped 81 PostgreSQL-only tests, and
found two compatibility/test-inventory failures. The original client-not-found
message was restored and the data-free review shell was explicitly classified
as public; both corrections passed in the focused verification above.

[Draft PR 27](https://github.com/boonewh/pathsix-backend/pull/27) targets staging.
[PostgreSQL CI](https://github.com/boonewh/pathsix-backend/actions/runs/37542613105)
passed all 647 tests with no skips at `1d0a74d`, including migration rehearsal,
restricted-role privileges, immutable receipts and concurrent confirmation.
Final review also found and repaired a mismatch between accepted field lengths
and database storage limits; 33 focused checks passed locally with two
PostgreSQL-only skips after that repair. CI must pass again on the final revision.

Staging migration rehearsal/application, deployment and real ChatGPT creation
remain pending. Fly currently needs a fresh local sign-in. No production
deployment or live grant change has been made by this increment.
