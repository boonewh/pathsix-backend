# PathSix ChatGPT MVP plan and readiness — October 6, 2026

This reconciles the historical September 6 frontend roadmap against the integrated
backend. The goal remains a remote MCP adapter over the same tenant-bound services
as the CRM, with delegated user consent. On October 6, the user approved a first
public release covering everyday read and write workflows before submission for
OpenAI review. The read-only staging pilot is the proven foundation, not the
intended complete customer release. The September 30 deployment and test evidence
below remains historical; this scope update does not establish a newer deployment.

## MVP scope lock and agent working rules

**Status: scope locked by the user on October 6, 2026; build incomplete.**
This MVP is the only planned product build until its completion criteria are met.
The approved workflow table, implementation steps and completion checklist below
define the work. Older roadmaps and historical gaps in this document do not add
tasks to the MVP. This lock supersedes the earlier read-only release target.

- Work toward completing this MVP. Make routine implementation choices and the
  supporting code, schema, test and UI changes necessary for its listed workflows.
  Prefer the smallest adequate solution using existing services. Do not add
  speculative scaling, general refactors, redesigns or extra tools as improvements
  to do along the way.
- The agent is allowed to investigate and fix discovered bugs or issues without
  asking for separate scope approval. Keep repairs bounded to restoring intended
  behavior, security, data integrity or reliability; document the issue, repair
  and relevant verification. A new capability or broad architectural improvement
  is not a bug fix merely because it would be useful.
- If the agent believes work outside this MVP should be done, state that it is
  outside scope, explain why it matters and its effect on the MVP, and ask the user
  before implementing it. Wait for an explicit answer for that work; continue
  independent MVP tasks. If it is a prerequisite, identify the blocked acceptance
  criterion rather than silently expanding the plan.
- Other issues and ideas may be recorded under **Future consideration** at the
  bottom. Recording an item does not authorize implementation or make it an MVP
  requirement. Do not work through that list during this build.
- Only the user can approve a scope change. Record their decision and update the
  affected scope and checklist before treating an addition as required. Do not
  reopen completed milestones merely to improve or scale them without a defect
  or an approved change.

These are scope rules, not a change to existing environment permissions or approval
requirements for operational actions. Once the MVP is complete, report the evidence
and remaining external release steps; do not automatically begin deferred work.

## Baseline and release status

Latest deployment: **staging v50 / `d64fe5c`**, October 26, 2026, repairs visible
review-result JSON/instructions with readable messages and a v3 card resource.
705 backend tests, 12 component tests and 20 deployed inline checks passed.
Phone save (lead 13) and cancellation outcomes are verified on v49; the v50
presentation retest still showed the old message. Metadata refresh and a fresh
manual retest remain required; see the stale-card investigation below.

### Previous v49 baseline

The previous verified deployment is **staging v49 / `6b8d898`**, October 25, 2026
(America/Chicago), with private MCP timing diagnostics and one staging web machine
kept running to avoid scale-to-zero read failures. [Full CI](https://github.com/boonewh/pathsix-backend/actions/runs/40262205811)
passed 705 backend tests with zero skips and 12 component regressions. All 30
deployed checks passed with public data, the restricted role and 18-table/25-policy
contract unchanged. Both machines passed health. The last 12 direct connector
reads passed, including a five-minute idle return and a batch on each host.
One earlier post-restart error is still unexplained, so returning-use reliability
remains open. The October 26 fresh-chat trial retained consent and completed two
successful CRM reads, but desktop ChatGPT showed "This response couldn't load".
The user's phone screenshots now confirm the same conversation contains the
correct saved answer (lead 12, status new) and the plain-text control response.
CRM retrieval, answer generation/persistence and phone display are verified;
desktop display of particular saved messages remains defective. No MCP schema
or consent repair is indicated by this symptom. Exact desktop cause is unknown.
This is phone viewing of an existing chat, not a fresh phone CRM workflow test.

Live ChatGPT verifies ordinary three-scope consent, refresh rotation, lead reading,
inline rendering and user-confirmed creation of lead 11. The v47 trial exposed
stale assistant text below the saved card; v48 fixes the missing follow-up request.
The fresh user-driven trial created exactly one lead (ID 12) and displayed the
automatic conversation confirmation, verifying the desktop saved-result fix.
The live inline cancellation trial also passed with conversation confirmation
and zero matching saved leads.
Existing lead 11 still recovers after reload without sign-in. No manual
OAuth URL edits were part of the successful trial. Production is unchanged;
October 25 returning-connector checks confirm retained consent and scopes without
sign-in, but two initial read errors required retries; seamless returning-use
reliability remains open. step 1 remains incomplete. See [lead-creation evidence and limits](mcp-lead-creation.md).

### Live reconnect diagnosis - October 24, 2026

Before the October 24 repair, staging was on `ee4cbb2` with inline review enabled. Read-only database
diagnostics verified code exchange and refresh rotation for today's ordinary
reconnect. At 19:10:50 UTC the user granted `clients:read leads:read`;
`list_leads` and `get_lead_options` succeeded at 19:10:58 and 19:10:59. At
19:11:04 `prepare_lead_creation` was denied because `leads:create` was absent.
The action count remained two (the earlier October 7 trials): this attempt did
not prepare a new proposal or create a lead. Evidence in the outer checkout:
`temp/mvp-lead-creation/consent-20261024.json` (scope/timestamp/audit metadata only).

Browser inspection reproduced a separate failure: the conversation's Reconnect
button displayed "This app does not provide a browser setup URL right now" before
opening PathSix. Reloading the chat did not produce a working authorization
destination. Settings -> PathSix CRM -> existing account -> Reconnect did open
the staging consent screen, but requested the same two read scopes. Repeating
that consent cannot provide lead-creation access. The agent did not submit it,
edit its URL, or widen a grant. Refresh tools was attempted in Settings without
visible confirmation of updated definitions; no successful metadata refresh is
claimed. This agent session still exposed the earlier external-review instructions,
which does not establish what a new ChatGPT conversation would receive.

The backend is reachable and authentication succeeded. The immediate blockers are
the in-chat reconnect launch and acquisition of the required write permission.
Whether the launch failure originates in saved app metadata or ChatGPT's UI is
not established. Do not count this as a passed card trial or assume OpenAI review
will resolve it. Keep step 1 incomplete and PR 27 in draft. Application source,
deployment, and production configuration were unchanged during this diagnosis.

Deployed connection repair: authenticated tool discovery now filters by the
registered AI client's allowed capabilities, rather than the current token's
already-approved scopes. This lets a returning read-only account discover the
write permission it needs to request. Per-tool OAuth metadata, record access,
execution-time scope checks, explicit consent and refresh scope restrictions
remain enforced. Seven new tests cover discovery-driven consent in both supported
protocols, preservation of read-only tokens, client catalog restrictions and
expired/revoked credentials. Local focused verification passed 109 tests with six
PostgreSQL-only skips. The first full CI run passed 701 tests but rejected the new
expiry test's attempt to mutate an immutable credential; the test now advances
the clock, and the corrected full run passed all 702 tests without skips.

Actual ChatGPT verification after deployment: before refresh, the app details
listed five read tools and no write tools. Refreshing the existing app against
the repaired server listed six read and two write tools with the new in-chat
review description. Settings -> existing account -> Reconnect then generated an
unmodified authorization request containing `clients:read leads:read leads:create`.
The visible PathSix consent page explicitly included reviewed lead creation.
The user completed sign-in/consent. Read-only diagnostics confirmed the new grant
at 19:45:20 UTC (2:45 PM America/Chicago), authorization-code exchange, and two
refresh rotations retaining all three scopes. The earlier read-only grants remain
unchanged. No grant was manufactured or widened by the agent. Evidence is in
`temp/mvp-lead-creation/consent-20261024-after-repair.json` in the outer checkout.
The fresh ChatGPT prompt succeeded: lead lookup and options at 19:46:55/56 UTC,
proposal preparation at 19:47:03, and receipt retrieval at 19:47:09. User screenshots
show the inline review card with the correct staging account, exact company name,
normalized fields and enabled Create lead/Cancel controls. Read-only database
verification confirms action `1c16a6ef-fbeb-417b-854d-c1cedbd63ad4` is pending,
with no result, no decision and no matching saved lead. It expires at 19:57:03 UTC
(2:57 PM America/Chicago). Evidence: outer-checkout
`temp/mvp-lead-creation/inline-live-pending-20261024.json` and the user's two
October 24 screenshots. This verifies real host rendering and preparation, not
creation, cancellation or interrupted-response recovery.

The host displays a `CSP off` badge during this developer-mode trial; production
CSP enforcement is not established by the rendering evidence. The model also
shows a separate summary and external review link below the usable card. Record
these observed host/UI limits without claiming complete host security or final
customer-flow acceptance. Step 1 remains incomplete pending the user's decision
and saved-result/recovery verification.

Release image: `registry.fly.io/pathsixsolutions-backend-staging:connection-bd87fbc`,
digest `sha256:e294af143b92c609e6fb4ec95975467777ade1511a5f6be4423652a311dd7981`.
Runtime revision: `bd87fbcf2a18b8faf8cc41ad420fa31921d25b93`. No migration or catalog
permission change was required. Rollback is the v46 image
`registry.fly.io/pathsixsolutions-backend-staging:deployment-01M4GJS79QNCNXJZ9F7ZVT24RJ`
with its `ee4cbb2` revision and existing staging resource configuration. Source,
test archive and seven runtime file hashes were checked by the deployed verifier.
Outer-checkout evidence: `temp/mvp-lead-creation/discovery-deployed-tests.json`,
`discovery-preflight-before.json`, `discovery-machines-after.json`,
`discovery-catalog-before.jpg`, `discovery-catalog-after.jpg`, and
`discovery-consent-after.jpg`. Production remains unchanged; PR 27 stays in draft.


## Conversation result repair - October 24, 2026

The real ChatGPT trial created `MVP-LEAD-20261024-INLINE`, lead **11**, after the
user clicked Create lead. Read-only verification confirmed action
`1c16a6ef-fbeb-417b-854d-c1cedbd63ad4` committed at 19:50:45 UTC, exactly one matching
lead and successful decision/confirmation audits. Evidence is in the outer
`temp/mvp-lead-creation/inline-live-committed-20261024.json` and the user's screenshot.
This supersedes the earlier pending-proposal evidence. The card displayed the
saved lead, but the entire assistant summary below it stayed pending.

The card previously sent only `ui/update-model-context`, which supplies context
for future turns. The repair requests a `ui/message` follow-up after a definite
result from a user decision or its read-only recovery. It shares only the action
ID, terminal status and saved lead ID; private metadata, account details, review
URLs and CRM text are excluded. The [official UI guidance](https://developers.openai.com/plugins/build/chatgpt-ui)
identifies this bridge method for follow-ups. The host owns the conversation:
earlier assistant text and preparation badges cannot be directly rewritten.
Tool guidance asks for a short card pointer instead of a duplicate pending table.

Notification attempts are consumed once within the mounted card. Refresh and
remount never generate duplicate messages. A messaging failure is shown separately
from the successful record operation; no notification failure retries a write.
Uncertain writes remain locked until receipt recovery. Hosts without advertised
text-message support keep the confirmed card and show the notification limitation.
The advertised resource is v2, with v1 reads retained for existing conversations.

Local verification: 12 Node bridge regressions, 26 backend tests (one PostgreSQL
concurrency skip) and 22 browser checks at 390px/1000px passed. Bridge regressions
run in CI alongside the backend suite. They cover save/cancel, recovery, unknown
outcomes, expiry, unsupported/rejected/timed-out messages, context failure, remount,
duplicate suppression, private-field exclusion and foreign receipts. The browser
checks also verify actual rendering and recovery against a synthetic local CRM.
Staging **v48 / aa3ca14e214905aadc75cbae49712bc050cadb32** now deploys this repair.
[Full CI](https://github.com/boonewh/pathsix-backend/actions/runs/40127746409) passed
702 backend tests with zero skips and all 12 bridge regressions. All 27 deployed
connection/inline tests passed with public rows/sequences, the 18-table/25-policy
contract, restricted role and immutable action trigger unchanged. Disposable
schemas were cleaned. Both machines passed health and normal auto-stop is restored.
Image: `registry.fly.io/pathsixsolutions-backend-staging:conversation-aa3ca14`,
digest `sha256:d1feffabd5a15e29159eb8612fa8828fbf0bc89023ca9206f28b701e6e999cde`.
Rollback uses v47's `connection-bd87fbc` image and `bd87fbc` revision; no schema,
scope or consent changes are required by this repair.

The existing ChatGPT connector was refreshed and its app details visibly show the
new preparation instructions. Reloading the original chat recovered saved lead 11
without another login or decision. The original assistant summary remains history.

The user completed the fresh `MVP-LEAD-20261024-CONFIRM` trial. Its card shows Lead
created, ID 12, and the conversation now confirms the saved lead and committed
status beneath the short card pointer. The user screenshot verifies the missing
conversation confirmation is present. Read-only staging verification confirms
action `1b13fe0e-9b6e-45eb-b0b2-eab7394b6ddb` committed at 20:13:08 UTC (3:13 PM
America/Chicago), result ID 12, exactly one matching lead and successful
decision/confirmation audit records. This passes the desktop happy-path save and
automatic-reporting trial; it does not establish the remaining failure cases.
Evidence: outer `temp/mvp-lead-creation/conversation-live-confirmed-20261024.json`
and `conversation-live-confirmed-20261024.png` (the user's supplied screenshot).
Outer-checkout evidence: `conversation-deployed-tests.json`,
`conversation-preflight-before.json`, `conversation-machines-after.json`,
`conversation-catalog-after.jpg` and `conversation-trial-draft.jpg`, all under
`temp/mvp-lead-creation`. The live desktop saved-result reporting defect is verified fixed.
Actual-host interrupted-response recovery, returning use, phone acceptance, metadata isolation
and production CSP remain live acceptance gates. Keep step 1 incomplete and PR 27
in draft; production is unchanged.

## Cancellation and interrupted-response checks - October 24, 2026

Six additional controlled browser scenarios passed at 390px and 1000px using the
unchanged v48 component. The test host discards the entire decision response after
the synthetic CRM has committed, sends no JSON-RPC reply or error, and waits for
the component's real 30-second request timeout. Each scenario recovered after
approximately 31 seconds:

- Cancelled decision: terminal cancelled receipt, zero new leads and no creation
  confirmation audit, exactly one decision request and one conversation message.
- Saved decision: the original committed receipt and lead ID, exactly one new lead
  and confirmation audit, one decision request and one conversation message.
- Saved decision plus failed receipt reads: Create stays disabled and no success
  message is sent while the outcome is unknown. Restoring reads and pressing
  Refresh status recovers the original saved lead and sends one confirmation.

Refresh and remount preserve all terminal results without additional writes or
messages. No browser script errors occurred. The tested HTML SHA-256 is
`84aecf1ab92430440f1a02408fc488a1aa2df84ac033a1e5652d492e08a73038`, matching the
source hash verified on deployed v48. No application source, deployed configuration,
production data or customer credentials were changed for these tests.

Local Hypercorn logged TLS shutdown errors (`APPLICATION_DATA_AFTER_CLOSE_NOTIFY`)
while browser contexts were being closed. All six receipt/data assertions and
browser script-error checks passed; the local transport teardown messages remain
a harness limitation, not evidence of a production transport failure.

This is controlled fault injection in a loopback MCP host and synthetic CRM, not
an induced interruption in the actual ChatGPT host. Evidence and reproducible
helpers are in the outer `temp/mvp-lead-creation/`: `recovery-preview.py`,
`prepare-recovery-harness.py`, `recovery-browser.cjs`,
`recovery-browser-results-20261024.json` and `recovery-*.png` screenshots.

The separate live ChatGPT cancellation trial passed. The user submitted
`MVP-LEAD-20261024-CANCEL-INLINE` and clicked Cancel. Their screenshot shows both
the card and conversation confirming cancellation and no saved lead. Read-only
staging verification confirms action `2f6dbc86-8994-4ba4-ada9-3d836ecabaad` is
terminally cancelled, result_id null, and there are zero matching leads (including
deleted rows). The successful decision/cancellation audits are at 22:24:10/11 UTC
on October 24 (5:24 PM America/Chicago). Evidence in the outer
`temp/mvp-lead-creation/`: `cancellation-live-20261024.json` and the user's
`cancellation-live-20261024.png` screenshot. No application deployment was needed.

A controlled actual-ChatGPT transport interruption remains unverified. Do not
mark that live acceptance gate complete based on the isolated tests. Desktop
save/confirmation, inline cancellation/confirmation and saved-receipt reload
recovery now have live evidence; step 1 remains incomplete pending the other
customer-flow and host-security acceptance checks.

## Returning connection check - October 25, 2026

The existing PathSix CRM connector in this desktop conversation successfully read
lead 12, its committed receipt (`1b13fe0e-9b6e-45eb-b0b2-eab7394b6ddb`) and the
cancelled receipt (`2f6dbc86-8994-4ba4-ada9-3d836ecabaad`) without sign-in, reconnection,
new consent, catalog refresh or a CRM write. This is direct connected-tool evidence,
not a new browser/phone conversation or a new human-approved write trial.

Read-only staging diagnostics confirm the same October 24 19:45:20 UTC grant remains
active, with clients:read, leads:read and leads:create unchanged. Refresh rotations
on October 26 at 02:47-02:48 UTC (October 25, 9:47-9:48 PM America/Chicago) retain
all three scopes, and successful tool audits reference that original grant.
The grant was not recreated or widened. No application/configuration change was made.

Connection retention passes, but this is not a clean reliability pass. Two of the
first three simultaneous reads returned generic MCP -32603 Internal error; the
committed receipt succeeded. Both failed reads succeeded when retried individually,
and a later parallel pair also succeeded. Fly logs show a normal machine wake-up
at 02:47:42 UTC and passing health by 02:47:49, plus successful refresh responses.
No corresponding application exception was found in the bounded log sample.
Cold-start timing is a possible contributor, not a proven root cause. The second
diagnostic machine was explicitly started only after the original connector checks
to read audit metadata; that does not explain away the initial failures.

Open bounded reliability issue: reproduce and diagnose the returning-read failures
before marking seamless returning use complete. Do not require the user to reconnect
as a workaround or treat successful retries as evidence that the defect is fixed.
Fresh browser/phone use and actual-host interrupted-response acceptance remain open.
Evidence in outer `temp/mvp-lead-creation/`: `returning-connector-results-20261025.json`,
`returning-consent-20261025.json`, and the local bounded `returning-logs-20261025.jsonl`.

## Returning-read diagnosis - October 25, 2026

A controlled cold-start reproduction returned two generic MCP -32603 errors
during machine wake-up; the third concurrent read succeeded.
At 02:57:36 UTC on October 26 (October 25 locally), the three calls began.
Client failures returned around 02:57:42; the server logged its listener at
02:57:45.859. These clocks are not synchronized evidence of exact ordering.
Fly logged PM03 (machine wake-up timeout) at 02:57:44.621. All three reads had
passed warm at 02:54:28, and both failed reads succeeded after startup without
reconnection. This establishes a scale-to-zero startup failure path, not a loss
of consent. No lead or proposal was created by these diagnostic reads.

The staging service minimum is now one existing machine in iad, preserving
auto-stop for extra capacity and automatic start. The source fly.staging.toml
matches both live machine service settings. This increases idle staging compute
usage; machine count, size, image, command, environment and database are unchanged.
Fly configuration validation passed. The original October 24 grant still has
all three scopes and is not revoked; write-action count remains five.

This does not yet close the reliability issue: the first batch after the
configuration update returned one error at 03:00:15 while both machines were
healthy; two reads passed. Slow database queries appeared in the same interval,
but that is correlation, not a proven cause. Its individual retry passed.
Do not claim one warm machine fixes every initial read error.

A bounded diagnostic patch adds generated request IDs, start/end times, HTTP
status, authentication duration and a fixed processing phase. Unexpected tool
handler failures emit a fixed marker. It logs no headers, arguments, CRM fields,
account IDs, response contents or exception details. SDK debug logs stay disabled.
Staging v49 now runs `6b8d898e5041e61c8d3dec96ac472e22ddff93a9`.
[Full CI](https://github.com/boonewh/pathsix-backend/actions/runs/40262205811)
passed 705 backend tests with zero skips and all 12 component regressions.
Focused local checks passed 56 tests with three PostgreSQL-only skips; the
30 revision-pinned deployed checks passed without skips. Public rows/sequences,
the restricted runtime role, 18-table/25-policy contract and immutable action
trigger were unchanged; disposable schemas were cleaned. Both machines passed
health, retain auto-start/auto-stop and have the one-machine minimum in iad.

The next four concurrent three-read batches all passed (12 reads): an initial
idle return, a return after more than five minutes without connector calls, and
one batch on each deployed host tested separately. The five-minute return ran
with one machine naturally stopped and the other retained. Deployed request logs
show HTTP 200 and 54-443ms server durations on both hosts, with no unexpected
handler marker in those test intervals. Generated IDs and fixed timing labels
were verified in live logs. Client/server timestamps are separate clocks; these
are server-measured durations, not inferred network latency. No deliberate
transport interruption or new browser/phone conversation is claimed.

Read-only final diagnostics confirm the original October 24 grant, all three
scopes and five write actions are unchanged. No reconnection, new consent or
CRM record write was needed. The scale-to-zero failure path is addressed; the
single post-restart error remains unexplained and did not recur in these batches.
Returning-use reliability stays open for a fresh real-host trial with diagnostics
enabled. Do not infer that the isolated warm error was caused by slow SQL or
that successful retries prove it fixed. Production is unchanged and PR 27 is draft.

Image: `registry.fly.io/pathsixsolutions-backend-staging:reads-6b8d898`, digest
`sha256:b5954795b7172ceaed6d2a7782c51ca588fc6ce4e2473d50df4687bf621f5660`.
Code rollback is v48 `conversation-aa3ca14`, digest
`sha256:d1feffabd5a15e29159eb8612fa8828fbf0bc89023ca9206f28b701e6e999cde`;
retain the one-machine minimum when rolling back only diagnostics. No migration,
permission or database resource change was made. Keeping one existing web machine
running increases staging idle compute usage; no additional machine was created.

Evidence: outer `temp/mvp-lead-creation/diagnosis-deployed-tests.json`,
`diagnosis-deployed-batches.json`, `diagnosis-final-timing-summary.json`,
`diagnosis-consent-final.json`, `diagnosis-final-machines.json`, and
`read-diagnosis-idle-20261025.json`. Raw bounded logs remain local.


Local evidence is in outer temp/mvp-lead-creation/read-diagnosis-*.json/jsonl,
including cold-start logs, before/after machine configuration, call results and
read-only consent verification. Roll back the hosting change by returning the
service minimum to zero in both live machines and fly.staging.toml; doing so
reintroduces the reproduced sleep/wake failure risk.

## Fresh-chat returning-use check - October 26, 2026

**Result: connection retention and CRM reads pass; end-to-end answer delivery fails.**
A new Chrome ChatGPT chat selected the existing PathSix CRM app through the normal
mention picker, then sent: "Find the lead MVP-LEAD-20261024-CONFIRM and show its ID
and status. Do not create or change anything." No sign-in, reconnect, new consent,
metadata refresh or manual scope change was needed. No connector call was made
from Codex to warm the service first. Before the trial, one staging machine was
running and one stopped, both configured for the one-machine minimum on v49.

The [test conversation](https://chatgpt.com/c/6adee3c1-2af0-83e9-a877-3e4475cbf989)
completed its first attempt ("Worked for 9s") without a visible answer. Expanding
the work showed "Found matching CRM leads" and "This response couldn't load".
Reloading showed the same load error, not a restored answer. One normal
"Try again" attempt completed ("Worked for 8s") with the same blank answer and
load error. This is a failed customer-flow check; eventual backend success does
not satisfy the acceptance requirement to return the answer in the conversation.

Read-only audit evidence confirms successful list_leads calls at October 26
05:23:21.705070 UTC and 05:24:47.596209 UTC. Both use the original October 24
19:45:20.466591 UTC grant with clients:read, leads:read and leads:create unchanged.
Refresh responses were HTTP 200. MCP requests completed HTTP 200 in 277.24ms and
426.44ms, with no unexpected-handler marker in the captured intervals. The write
action count remains five. This establishes retained authorization and successful
CRM reads, not the exact cause of ChatGPT's missing answer. No raw response payload
was logged. The earlier isolated warm-read error is not explained by this trial.

Next bounded investigation: distinguish ChatGPT response loading/rendering from
an integration response-contract issue, using the recorded conversation and the
existing valid tool-result tests. Do not change OAuth consent, weaken security,
reconnect as a workaround, or mark returning-use reliability complete based on
these successful audits. Phone acceptance and the other existing gates remain open.
No application code, runtime configuration, CRM record or production change was
made during this check. PR 27 remains draft.

Evidence in outer temp/mvp-lead-creation: fresh-chat-summary-20261026.json,
fresh-chat-machines-before-20261026.json, fresh-chat-final-consent-20261026.json,
fresh-chat-final-logs-20261026.jsonl, and screenshots fresh-chat-blank-20261026.png,
fresh-chat-after-reload-20261026.png and fresh-chat-retry-failed-20261026.png.
The visible result tab is retained for inspection. Raw bounded logs remain local.

## Missing-answer isolation - October 26, 2026

**Diagnosis: this observed response-loading failure also occurs without PathSix
or any tool use. It is broader than the CRM integration; the exact ChatGPT-side
or browser/account cause remains unverified.** Do not patch CRM response handling
or consent based on the missing-answer symptom alone.

The same list_leads request through the connected tool returned lead 12 with
status new. Its actual structuredContent validates against the declared output
schema, and its JSON text content equals the structured data. The read tool has
no UI resource metadata, and the result has no component metadata. This matches
the [documented result contract](https://developers.openai.com/plugins/reference#tool-results).
The validation used the serialized SDK field aliases; two preliminary local
inspection attempts failed on test-harness setup (SQLite pool options, then a
Python field-name alias), not on a server response. The corrected check passed.
No application source was changed.

Two controlled prompts then asked only for the fixed text PATHSIX-DISPLAY-OK:

- In the original CRM conversation, a no-tools/no-CRM prompt also completed with
  "This response couldn't load".
- In an independent [Display check conversation](https://chatgpt.com/c/6adee56b-40ac-83e9-a0da-4763a8100060),
  with no plugin selected, the prompt "Display check only: reply with exactly
  PATHSIX-DISPLAY-OK. Do not use tools, plugins, apps, or search." produced the
  same load error. Copy reported Copied but supplied empty text. The thread reader
  reported a completed turn and a content reference, not accessible answer text;
  that does not establish whether generation or content retrieval failed internally.

Final read-only audit inspection found no CRM calls from either plain-text
control. The latest audit remains the diagnostic list_leads call at 05:28:47.950554
UTC. The original three-scope grant remains active and the action count is five.
Browser console inspection exposed only a Statsig cache warning, not a diagnostic
stack trace establishing causation. Opening the independent control conversation
in the in-app browser reached a signed-out login page, so cross-browser/account
isolation was not completed. No credentials, cookies, extensions or account
settings were changed, and the signed-out comparison tab was closed.

The [public OpenAI status page](https://status.openai.com/) reported operational
when checked; it explicitly reports aggregate availability. This does not rule
out an account/browser-specific issue and is not proof of a service-wide incident.
The next discriminating check is the independent plain-text control in another
signed-in browser/device. If it also fails, the non-CRM reproduction and affected
conversation are ready for OpenAI support. No support message was sent. Re-run the
fresh CRM chat only after ordinary answers render; keep the acceptance gate open.
The earlier isolated warm-server read error remains a separate unresolved item.

Evidence in outer temp/mvp-lead-creation: answer-diagnosis-result-20261026.json,
answer-schema-validation-20261026.json, answer-diagnosis-consent-20261026.json,
answer-diagnosis-logs-20261026.jsonl, answer-no-tool-control-failed-20261026.png,
and answer-independent-control-failed-20261026.png. Both relevant Chrome chats
are retained. Runtime stays v49 / 6b8d898; no deployment, CRM record change or
production change was made. PR 27 remains draft. No new regression suite was
required for this read-only diagnosis and evidence-only documentation change.

## Phone confirms the saved answer - October 26, 2026

User-supplied phone screenshots of the existing Find lead status conversation
show the complete CRM answer: MVP-LEAD-20261024-CONFIRM, lead ID 12, status new,
and the statement that no records were created or modified. The subsequent
no-tool control in that conversation also displays PATHSIX-DISPLAY-OK.
The user additionally reports both control answers in Display check render on
phone, while their desktop screenshot shows the original unavailable and a later
manually requested control answer visible.

This supersedes the earlier uncertainty about answer generation/persistence:
the CRM lookup completed, and ChatGPT generated and saved a correct answer
that another client can display. The remaining observed failure is desktop
loading/display of particular saved messages, not missing CRM data or a missing
saved answer. No evidence supports changing the MCP result schema or OAuth
permissions to repair this symptom. The precise desktop delivery/rendering
mechanism remains unverified; do not attribute it to caching, an extension or
a particular model without evidence.

Acceptance accounting: retained consent, successful fresh-chat read, correct
saved CRM answer and phone display of that answer are verified. Desktop display
reliability remains open. This was viewing the existing conversation on a phone,
not a fresh phone-originated CRM lookup or a phone creation/cancellation/recovery
trial. Do not mark all phone acceptance or step 1 complete. The separate isolated
warm-read error also remains unresolved. Runtime stays v49 / 6b8d898; no code,
deployment, permissions, CRM record or production change was made for this update.

Evidence in outer temp/mvp-lead-creation: phone-saved-answer-evidence-20261026.json
records the user report and SHA-256 hashes of the unchanged supplied photos,
phone-saved-crm-answer-20261026.jpg and
phone-saved-answer-and-control-20261026.jpg. The existing backend audit and
schema-validation evidence remains applicable. PR 27 stays draft.

## Phone creation verification - October 26, 2026

The user manually ran the phone creation trial for
`MVP-LEAD-20261026-PHONE-CREATE`. Their screenshot shows an automatic saved
confirmation identifying lead 13. Independent read-only connected-tool checks
confirm action `8e11c9e5-0897-4b53-9fca-979355a66590` is committed to lead 13.
An exact-name search returned one accessible matching lead, status new, with no
further page. This establishes the saved receipt and one visible matching record;
it is not a database-wide duplicate/audit attestation.

The screenshot also exposes the component's raw `PathSix review result` JSON and
model-directed follow-up instructions in the conversation before the friendly
confirmation. Record this as an open in-scope customer-experience defect: the
save and follow-up worked, but phone presentation is not ready for acceptance.
Investigate the supported host message contract before changing the notification;
preserve exact-action approval, receipt recovery and duplicate protection.
The subsequent manual phone cancellation trial also completed. Screenshot
`1000023342.jpg` shows automatic cancellation confirmation. Independent read-only
checks confirm action `05fddcf7-b5fd-44bf-b1a0-e1ae01525e41` is cancelled;
searching `MVP-LEAD-20261026-PHONE-CANCEL` returns no accessible matching leads
and no further page. Evidence: outer
`temp/mvp-lead-creation/phone-cancel-verification-20261026.json`.
The cancellation message exposes the same raw JSON and follow-up instructions,
so the presentation defect affects both terminal outcomes. Phone save and cancel
outcomes are verified, while full phone acceptance and actual-host interruption
remain open. Neither screenshot establishes fresh phone consent or absence of
reconnect prompts.

Evidence: user attachment `1000023340.jpg` and outer
`temp/mvp-lead-creation/phone-create-verification-20261026.json`.
No runtime change or deployment was made for this verification.

## Readable review follow-up repair - October 26, 2026

Phone trials exposed raw JSON and model instructions from our `ui/message`
payload. This is a component-authored chat message, not a hidden instruction
channel. The component now sends only a short factual result: saved with lead ID,
cancelled with no lead created, or expired with no lead created. Receipt action
ID/status remain in `ui/update-model-context` structured content. Neither channel
includes review capabilities, account data, review URLs or user-authored fields.
The same once-per-decision notification, unknown-outcome recovery and no-write-
retry behavior remain. The visible message remains self-contained if the context
update fails. No claim is made that hosts hide the readable component message.

New cards advertise `ui://pathsix/lead-review-v3.html` to avoid cached v2 HTML;
v1 and v2 resources still resolve to the repaired component for existing chats.
The component bridge suite passes all 12 tests, covering save/cancel/expiry copy,
private-data exclusion, failed context/message delivery and response recovery.
Exact-revision CI passed 705 backend tests with zero skips and all 12 component
checks: https://github.com/boonewh/pathsix-backend/actions/runs/40371492217 .
Staging v50 deploys `d64fe5c998d5d4ee992e0827d38bab7e65b0ad0c`, image digest
`sha256:0f8aca62b9e7fa7c431610e610c6566cb3e508e66de7d60992a2bb2cd81a69cf`.
All 20 deployed inline-review checks passed, zero skips. Deployed source hashes
match the clean commit archive; public rows/sequences, restricted role, 18-table /
25-policy contract and action trigger are unchanged; disposable schemas are clean.
The active machine passed deployment health and public HTTPS health returned 200.
Both machine configurations use the repaired image and revision, with one ready
machine and automatic spare shutdown retained. No schema or grant change occurred.

Evidence in outer temp/mvp-lead-creation: phone-deployed-tests.json,
phone-verification-input.json, phone-machines-after.json, phone-releases-after.json.
Rollback is the prior v49 image digest
`sha256:b5954795b7172ceaed6d2a7782c51ca588fc6ce4e2473d50df4687bf621f5660`
with APP_REVISION `6b8d898e5041e61c8d3dec96ac472e22ddff93a9` and existing staging
configuration. A manual fresh-card phone retest is still required before closing
the presentation defect. Existing transcript messages cannot be retroactively changed.

References: https://developers.openai.com/plugins/build/chatgpt-ui and
https://developers.openai.com/plugins/reference (component-authored follow-up and
structured model context). This repair does not change permissions, writes or
production configuration.

## Fresh-phone stale-card investigation - October 26, 2026

The user confirmed CLEAN-CANCEL was prepared in a brand-new ChatGPT phone
conversation after v50 deployment. Screenshot `1000023344.jpg` still shows the
old raw JSON/instructions; therefore the presentation retest failed, not passed.
Connected-tool verification confirms action
`be1cdf01-4a97-4cc5-b80c-873275b22174` is cancelled and a search for
`MVP-LEAD-20261026-CLEAN-CANCEL` returns no accessible matching lead.

A subsequent read-only check on the running staging machine confirms revision
`d64fe5c`, the expected component SHA-256
`db065815bfce847d023215f89ec868df2944ba096b72e4b4adcacbe32a68192e`, no old message
literal, and the readable cancellation text. Both preparation and receipt tool
metadata advertise `ui://pathsix/lead-review-v3.html`. Machine configuration
inspection after the failed trial shows both machines on the v50 image/revision.
Evidence: outer temp/mvp-lead-creation/phone-card-cache-live.json and
phone-retest-machines.json. The server resource tests already verify v1/v2 aliases
serve the repaired HTML. No additional deployment or grant changes were made.

Official custom-MCP testing guidance requires refreshing connection metadata after
UI-resource changes, then starting a new conversation. That refresh was omitted
between v50 deployment and this trial. Retained app metadata/resource content is
the leading explanation, but ChatGPT's actual cached URI/content has not been
inspected, so the exact cache layer is unproven. Published-plugin guidance also
allows up to one hour of same-URI caching; this private custom connection should
use its documented Refresh flow, not infer published-plugin review timing.

Next: the user refreshes the existing PathSix CRM connection metadata in ChatGPT,
then runs a fresh manual cancellation trial. Do not uninstall, recreate grants,
or widen permissions. If Refresh is unavailable, inspect the visible connection
screen rather than guessing another control. The defect remains open until a
fresh phone trial displays readable text and the automatic confirmation.
Sources: https://developers.openai.com/plugins/deploy/connect-chatgpt#refresh-metadata
and https://developers.openai.com/plugins/deploy/app-review#other-changes .

### Earlier deployment history

Work resumed from staging `aa1d830`, which merges the September 22 handoff.
The earlier historical deployment was **v40 / `bf6af89`** after the client, report, storage,
import, user, preference, identity/platform, isolation-proof, AI consent, OAuth browser and read-only MCP increments. PostgreSQL/RLS CI
passed **594 tests with zero skips**; both
staging machines passed health checks and runtime inspection confirmed restricted
credentials with RLS enabled. Frontend `93c9618` was not changed or redeployed.
The earlier 33 browser regressions belong to the September 22 rollout. The new
backend-served OAuth screens have separate synthetic mobile/desktop browser evidence. See
[the earlier integration handoff](staging-main-integration-2026-09-22.md).

The client-service increment merged through PR #14 and first deployed as v30 /
`4301ba4`. See [client operations](client-service-operations.md). The report-service
increment merged through PR #15 and deployed as v31 / `30c6ab2`; see
[report service](report-service.md) for verification, rollout and rollback details.
Storage operations now use StorageService through PR #17, deployed as v32 /
`2149bb6`. All 25 focused storage checks passed against deployed code in disposable
PostgreSQL schemas, with public users/files unchanged. See [storage service](storage-service.md).
Import orchestration now uses ImportService through PR #18, deployed as v33 /
`dfd8230`. All 25 focused import checks passed against deployed code in disposable
PostgreSQL schemas, with public data unchanged. See [import service](import-service.md).
User administration and personal preferences now use UserService and PreferenceService
through PR #19, deployed as v34 / `448a6c0`. All 40 focused user/preference and
password-reset checks passed against deployed code in disposable PostgreSQL schemas.
Public users, roles and preferences were unchanged. See
[user and preference services](user-preference-services.md).
Current-user configuration/password operations now use IdentityService, and platform
backup/restore/cleanup jobs have an explicit fail-closed worker boundary through PR #20.
Staging v35 / `6c8ce18` passed all 36 focused identity/platform checks, including
concurrent password changes. Public credentials and identity/configuration stayed
unchanged, and platform jobs remain disabled. See
[identity and platform boundaries](identity-platform-boundaries.md).
Session identity is now fixed across cached access, transaction lifecycle and request
reuse. A complete model/table inventory and deliberate predicate/policy failures
close the named current-web proof gaps through PR #21. Staging v36 / `898cc7a`
passed all 50 focused tests, the 14-table/20-policy live attestation, and unchanged
full-row digests across all 17 public application tables. See
[isolation proof](isolation-proof.md).
Owner-bound AI consent records and their management APIs now support explicit read
permissions, expiry, terminal revocation and current-state checks through PR #22.
Staging v37 / `3770a2d` passed 92 focused consent/migration/isolation checks, with
all 19 public application tables unchanged by verification. The two new tables were
empty at that release; no AI client or MCP tool was enabled. See
[AI connection consent](ai-connection-consent.md). The handoff documentation does
not change v37's verified application source. The subsequent OAuth browser flow adds
authorization-server discovery, explicit mobile consent, resource-bound opaque tokens,
refresh rotation/replay revocation and a backend-served connection management page.
Staging v38 passed 136 focused tests using disposable schemas over the private
database endpoint, with all 20 public application tables unchanged. An initial
Flycast connection drop and database resource-pressure signal remain recorded as
an open operational issue. See [OAuth browser flow](oauth-browser-flow.md).
The subsequent [read-only MCP pilot](mcp-readonly-pilot.md) adds client summaries,
protected-resource metadata and durable invocation auditing. Staging v39 passed
190 deployed checks with all 21 public application tables unchanged and both
machines healthy. ChatGPT was subsequently registered as `pathsix-chatgpt-staging`.
Staging v40 accepts ChatGPT's optional language hint without weakening OAuth
validation. The September 30 phone test completed sign-in, approval, code exchange
and refresh rotation, then called both `list_clients` and `get_client` successfully.
Backend audit records and actual ChatGPT tool history corroborate the two staging
client summaries. The initial list request exceeded the maximum page size and was
correctly rejected; ChatGPT retried with valid arguments. Production is not enabled.

## Reconciled gates

| Gate | Status | Evidence and remaining work |
| --- | --- | --- |
| 0: operational baseline | Partial | Integrated staging source and CI are reproducible. Staging DB sleep is documented as one hour. Original dirty checkouts are preserved separately. Full resource/cost inventory, restore drill and earlier staging DB-stall investigation remain open. |
| 1: immediate REST security | Completed documented milestone | Current server-side roles and active user/tenant checks, parent authorization, admin reports/import, protected calendars, disabled tenant backup API, JWT validation and reset protections. See reliability-security-2026-09-06.md. |
| 2: structural isolation | Current web and delegated-identity foundations implemented | Trusted fixed-identity web sessions, tenant services, restricted DB role, tenant FKs/indexes, 30 earlier same-tenant relationship FKs plus grant/credential/audit owner FKs, parent rules and forced RLS on 17 tables. Model inventory classifies all 21 tables. The narrow MCP adapter validates raw credentials and scopes before invoking ClientService; model caveats below remain open. |
| 3: adversarial isolation proof | Current web and limited client MCP proof implemented | Tests cover service, HTTP, file/object and platform boundaries plus session/cache reuse, all protected tables, aggregate secrecy and deliberate missing/weakened predicates/policies. Read-only policy attestation detects drift. The two client MCP tools add protocol, scope, revocation, ownership and audit-failure proofs. See isolation-proof.md and mcp-readonly-pilot.md; broader tools and operational durability remain separate gates. |
| 4: delegated AI authorization | ChatGPT staging connection verified | Owner grants, browser S256 consent, discovery, hashed short resource-bound tokens, refresh rotation/replay revocation, current-state validation and mobile connection management are implemented. Real ChatGPT sign-in and exchange passed; automated client registration remains separate. Grant IDs and web JWTs cannot authenticate delegated access. |
| 5: read-only MCP pilot | Real ChatGPT phone reads verified | Official SDK stateless transport; list_clients/get_client with clients:read, existing record permissions, bounded summaries, append-only owner audits and durable call limits. Both tools were called successfully from the user's phone. |
| 6: write tools | In-chat lead review candidate deployed; live card/confirmation/recovery and normal permission recovery pending | [Lead creation](mcp-lead-creation.md) adds separate consent, browser confirmation, atomic receipts and replay/partial-failure tests; 149 implementation, 10 scope-recovery and 20 subsequent in-chat approval deployed checks passed. Contact/project creation, updates, assignments/status changes, notes/interactions and optimistic concurrency remain required before submission. |
| 7: production MCP rollout | Not started | Threat model, external review, distributed limits, monitoring/runbooks and gradual opt-in pilot remain. |

## Service boundary inventory

- Search, leads, contacts, projects, interactions, accounts, subscriptions, recent
  activity and purge have tenant-bound service implementations.
- Client lifecycle was already extracted. This increment adds all client lists,
  activity filters/statistics, assignment and bulk soft deletion; the client HTTP
  module now has no direct ORM queries. Permanent deletion uses the existing shared
  PurgeService adapter.
- ImportService now owns bounded CSV/XLSX parsing, mappings, tenant-bound active
  assignee lookup and row orchestration through LeadService. Routes adapt requests,
  commit and send the prepared notification after commit. Direct service calls
  enforce admin authorization; row failures preserve batch rollback semantics.
- All report reads now use an admin-only ReportService, including
  the legacy summary alias and Sales Activity adapter. StorageService owns file
  metadata/object access with current-role checks, confined paths and ordinary-failure
  compensation. User administration now uses an admin-only UserService. PreferenceService
  scopes personal preferences by both user and tenant, including outside HTTP/RLS.
  IdentityService owns current-user/configuration reads and authenticated password
  changes. Login and signed-reset lookups remain authentication-boundary operations.
  Existing background jobs are global backup/restore/retention: their entry points
  now require an explicit operation allowlist and privileged PostgreSQL connection
  without tenant identity, and reject HTTP contexts. They remain outside tenant/MCP
  APIs and disabled on the web deployment. Operator/queue security, restore approval
  and actual backup/restore reliability still require review.
- Review remaining model issues separately, including global account-number
  uniqueness and polymorphic activity references. Database RLS protects tenant
  boundaries; record permissions within a tenant still require service predicates.

## Historical increments and release handoff

Items 1–10 summarize earlier work and its limits; they are not an additional work
queue. Item 11 hands off to the locked MVP below.

1. Client services are verified in PostgreSQL CI and deployed to staging.
2. ReportService is verified in PostgreSQL CI and staging. Report reads now have a
   reusable admin boundary; no MCP scope enforcement is implied.
3. StorageService is verified in PostgreSQL CI and deployed to staging. Durable
   storage/reconciliation and bounded file reads remain prerequisites for a file MCP
   pilot; staging currently has ephemeral local storage on two machines.
4. ImportService is verified in PostgreSQL CI and staging. Import idempotency and
   durable notification delivery remain prerequisites for MCP import writes.
5. UserService and PreferenceService are verified in PostgreSQL CI and staging.
   Current web identity/role checks are preserved; no delegated administrative grants
   or MCP account-management tools are enabled.
6. IdentityService and the fail-closed platform-job boundary are verified in PostgreSQL
   CI and staging. No real platform job was run or enabled. Keep the operational DB-stall issue
   visible; do not infer it is fixed from a successful CI run.
7. The current-web Gate 2/3 proof gaps are addressed by fixed-identity sessions,
   a complete model/table inventory, missing/weakened protection tests and policy
   attestation. See [isolation proof](isolation-proof.md) for the exact evidence
   and remaining limits.
8. Consent/grant storage and owner management are verified in PostgreSQL CI and staging.
   Negative scope, expiry, revocation, inactive-identity and concurrent-limit tests
   pass. The OAuth browser flow and backend connection-management screen are now
   implemented for operator-registered HTTPS clients. ChatGPT staging enrollment
   and the real phone test are now complete.
9. The read-only client MCP adapter and its adversarial proofs are implemented.
   The real ChatGPT staging consent and read trial is complete. Existing automated
   revocation tests remain the evidence for revocation, not a claim that the user's
   working ChatGPT grant was revoked.
10. Onboarding recovery now distinguishes expired approval requests, failed sign-in
    and unavailable service; both browser pages identify staging accounts and allow
    account switching. See [OAuth browser flow](oauth-browser-flow.md). Deployment
    and verification of this increment are recorded with its release.
11. Complete the approved everyday read and write workflows below before public
    submission. The bounded operational checks, production OAuth configuration,
    and submission materials in the MVP checklist remain release requirements. Private staging testing
    can continue while these increments are built. OpenAI approval and publication
    remain external milestones, not outcomes established by a successful connection.

## Locked MVP capabilities

The October 6 decision is to submit a useful CRM workflow integration, including
writes, rather than submit only the two client-summary tools. Customers should be
able to connect, sign in, approve specific access, and use their existing company
and record permissions through ChatGPT.

| Customer workflow | Existing foundation | MCP work required |
| --- | --- | --- |
| Find and review clients, leads, contacts and projects, with relevant interactions | SearchService and record services; two client-summary tools are verified | Bounded search/detail contracts and explicit read permissions for the additional records and fields |
| Create leads, contacts and projects | LeadService.create, ContactService.create, ProjectService.create | Scoped creation tools, parent selection, validated inputs, confirmation and durable duplicate-request protection |
| Update client, lead, contact and project details | Existing record update services | Explicit field allowlists, current-record checks, conflict detection and confirmed updates |
| Change assignments and statuses where the CRM supports them | Existing assignment operations and schema validation | Authorized assignee selection, current-role checks, existing status rules and safe handling of notification side effects |
| Record notes and interactions | Existing record notes fields and InteractionService | Clear append-versus-replace semantics for notes and confirmed, repeat-safe interaction creation |

Use these boundaries when implementing the table:

- Reads cover bounded lists/search and selected record details for the listed
  entities. Contact selection may use its accessible client or lead. Return only
  permitted fields and relationships; do not expose every model field or expand
  to accounts, subscriptions, reports or user administration because a shared
  search service can return those types.
- Writes operate on one selected record per action. An ambiguous name must be
  resolved before a write; never guess a parent or assignee. Limited lookup of
  eligible assignees and current allowed status values is supporting MVP work,
  not an administrative user-management feature. Preserve current backend and
  tenant-configured validation rather than introduce new business rules.
- Notes use existing note fields. Adding a note preserves existing content;
  explicitly requested replacement must show that intent and protect against
  stale updates. Interactions are recorded against an authorized existing parent.
  No new standalone notes product or automated outreach is required.
- MVP creation covers leads, contacts, projects and interactions. Client creation,
  lead-to-client conversion, deletion/restoration, bulk actions, file tools,
  report tools and platform/user administration are not included. Ask before
  adding any of them; existing CRM availability is not authorization to expose
  them to ChatGPT.

Exact tool names and field contracts are implementation choices within these
boundaries. Existing read-only connections must keep working with their original
permissions; adding tools does not silently expand an existing grant.

## Implementation order and acceptance criteria

### Customer experience requirement — approved October 7, 2026

The user explicitly confirmed that the finished experience must be easy: ask
ChatGPT to retrieve CRM information and receive it; ask to add a record and have
it saved, with only necessary clarification and approval. This is an acceptance
requirement for the locked workflows, not a deferred UI enhancement. It authorizes
the supporting connection and approval-flow changes, not additional CRM capabilities.

- Connect the intended PathSix account through normal sign-in and consent once.
  Routine use must retain that connection and its approved permissions across
  token refresh and new conversations. Reauthorization remains appropriate after
  revocation, expiry requiring sign-in, or a requested permission change.
- Retrieve authorized records from natural-language requests without manual
  scope editing, reinstalling duplicate apps, refreshing tool catalogs, or
  operator assistance. Ambiguous records require a concise clarification.
- Complete routine writes within the ChatGPT conversation, including a concise
  review and approval when required, and return the saved result automatically.
  Repeated CRM logins, separate review tabs and a further prompt just to check
  whether the save worked are not the accepted normal workflow. Target one
  explicit approval of the exact action; record any unavoidable host prompts.
- Preserve scope and record authorization, exact-action confirmation, atomic
  receipts/audits, replay protection and conflict handling. A model assertion
  of approval or a tool annotation is not a substitute for the approval boundary.
- Verify the normal path on desktop and phone, including a fresh connection,
  returning use, cancellation, one successful save, and recovery after a lost
  response. Record actual prompts/clicks and host limitations. Manual diagnostic
  workarounds and eventual OpenAI review do not satisfy this requirement.

The October 9 source increment implements a guarded in-chat review candidate;
[its evidence and host trust limits](mcp-lead-creation.md#in-chat-review-increment--october-9-2026)
are tracked separately from deployed behavior and live acceptance.

The currently deployed external review page is an interim implementation. A
minimal review inside ChatGPT is an implementation candidate supported by the
[official UI documentation](https://developers.openai.com/plugins/build/chatgpt-ui)
and [tool/UI metadata reference](https://developers.openai.com/plugins/reference).
First verify the host's approval contract and how the server binds a genuine
user decision to the existing proposal and authenticated owner. UI-only tool
visibility and hidden result metadata are documented mechanisms, not by themselves
proof that the current server can trust a click. Do not remove the working
confirmation boundary before its replacement passes the security and live-flow
checks. See the [server guidance](https://developers.openai.com/plugins/build/mcp-server)
and [authentication guidance](https://developers.openai.com/plugins/build/auth).

### Implementation sequence

1. Build one complete lead-creation workflow in staging: find relevant records,
   collect required fields, review the proposed lead, obtain explicit approval,
   create it once and return the saved result. Reuse LeadService rather than
   duplicate its validation or authorization in the MCP adapter.
2. Establish the shared write boundary in that increment. Existing read grants
   must remain read-only; added permissions require fresh consent. Authenticate
   and recheck current membership, record access, scopes and revocation at execution.
   Confirmation must bind to the exact proposed action and current caller; a
   model-supplied approval boolean alone is not proof of user confirmation.
3. Persist an idempotency receipt with the business change and audit in one
   transaction. Repeated or concurrent requests must not create another record.
   Reusing a key with different inputs must fail. A lost response after commit
   must have a recoverable outcome. Do not automatically retry ambiguous writes
   or send duplicate notifications.
4. Expand to contacts, projects and interactions, then updates, assignments,
   statuses and notes. Updates must detect intervening edits from both the CRM
   and MCP so stale proposals cannot overwrite newer work. Parent changes and
   assignment changes must retain existing visibility and tenant rules.
5. Verify each increment with isolated PostgreSQL/RLS tests and synthetic staging
   workflows. Cover revoked/expired/read-only grants, foreign or inaccessible
   records, invalid parents/assignees, concurrent/replayed requests, stale updates,
   rollback/audit failures and ambiguous response recovery. Include prompt-driven
   attempts to turn CRM text into authorization. Preserve existing read and web
   regressions. Run real ChatGPT end-to-end checks before calling a workflow ready.
6. Complete production readiness and rollout verification, then prepare the public
   package, listing/policy links, reviewer access, demonstrations and positive and
   negative workflow cases. Submit the implemented capability set for OpenAI review
   and publish after approval.

The immediate engineering milestone is the lead-creation workflow plus the shared
write protections above. This document change itself enables no new runtime
capabilities and is not evidence of a migration, deployment or submission.

The [lead-creation increment](mcp-lead-creation.md) is implemented on
`codex/mvp-lead-creation` with local backend, browser and PostgreSQL CI verification. Its report
tracks PostgreSQL and staging validation separately. This is progress toward
step 1, not completion of the MVP or permission to start a deferred feature.

## Definition of MVP build completion

Mark a criterion complete only with linked test or release evidence. The earlier
594-test milestone is the read-only baseline, not proof of the future write build.

- [ ] All five workflow groups in the locked capability table work end to end in
  ChatGPT against synthetic staging records, including cancellation, invalid
  input, ambiguous selection and insufficient permissions. Confirm resulting
  records in the CRM. Verify connection, consent and confirmation on phone and
  desktop, with ordinary-user and administrator permission cases.
- [ ] The approved customer experience above works through normal ChatGPT use:
  persistent account connection, natural-language reads, writes and necessary
  approval in the conversation, and automatic saved-result reporting. Fresh
  connection and returning-use trials pass on phone and desktop without manual
  scope fixes or routine external review/login steps. Document any host limitation
  for the user's decision rather than silently accepting a different workflow.
- [ ] Write consent, action confirmation, revocation, audit, idempotency and stale
  update protection meet the implementation criteria. Repeated/concurrent calls
  and lost responses cannot silently duplicate actions or overwrite newer work.
  Revoked or insufficient access cannot recover private data through a receipt.
  Notification failures are distinguishable from the outcome of a saved record.
- [ ] Relevant local and PostgreSQL/RLS CI checks pass, including existing web/read
  regressions and the new write failure cases. Staging cleanup is verified and
  any unrelated known failures are explicitly explained, not called a clean pass.
  No known blocking authorization, data-integrity or workflow defect remains.
- [ ] Operational preparation is complete for this tool set: shared abuse limits
  for OAuth/MCP entry points, privacy-preserving error/audit monitoring, actionable
  alerts, bounded audit/receipt retention consistent with replay protection, and
  a short incident/revocation/rollback runbook. Diagnose and fix database failures
  that block these workflows or trustworthy validation. This does not require an
  unrelated infrastructure redesign or a general scaling/performance program.
- [ ] A reproducible release and necessary migrations are rehearsed with rollback
  and backup/recovery checks for affected data. Verify the intended production
  OAuth client, configuration and deployed code, preserve existing production
  fixes, and confirm customer linking and basic reads. Validate writes using
  isolated synthetic records, not customer records. Record rollout evidence and
  unresolved operational limits rather than infer readiness from local tests.
- [ ] The submission package describes exactly the tested tools and permissions.
  Listing/policy/support information, reviewer access, positive and negative
  examples, and a demonstration are ready; claimed test cases have actually run.

When these checks are met, the build is ready for submission. Submission, OpenAI's
review decision and publication are separate release statuses and must be reported
accurately. A review request that requires extra capabilities or broader work
outside the locked scope requires the user's decision; ordinary defect repairs
remain allowed. Waiting for external review does not authorize backlog work.

## Future consideration

**Recorded only; not approved work and not additional MVP completion criteria.**
For new entries, record the observation, its impact, supporting evidence and any
user decision. Keep this section at the bottom. If an item becomes a concrete MVP
defect, a bounded repair is allowed under the rules above; otherwise ask before
moving it into the build.

| Item | Reason to revisit | Current disposition |
| --- | --- | --- |
| Client creation and lead-to-client conversion tools | Extend the sales lifecycle beyond the approved create/update set | Requires a scope decision |
| Delete, restore, bulk/import, file, reporting and administrative tools | Additional CRM workflows beyond this MVP | Requires a scope decision; file tools also need durable storage |
| Dynamic client registration/CIMD and broader AI-client support | Improve interoperability beyond the configured ChatGPT connection | Deferred unless required for the agreed connection; ask before expanding |
| Full resource/cost inventory and platform-wide recovery work | Older operational roadmap extends beyond recovery checks for this release | Deferred; release-specific recovery checks remain in the MVP |
| Global account-number uniqueness and polymorphic activity references | Known model-design questions in the isolation reports | Record specific defects if encountered; no general model redesign assigned |
| Durable file reconciliation, import notification infrastructure and platform workers | Support workflows not exposed by this MVP | Deferred; current MVP assignment side effects must still behave correctly |
| Broader scaling, generalized abstractions and UI redesign | Potential future improvements after real usage | Not part of this build |
| CI action/runtime maintenance | [Lead-creation CI](https://github.com/boonewh/pathsix-backend/actions/runs/37543228687) warns that checkout/setup-python target deprecated Node 20 and that ubuntu-latest is scheduled to change | CI passed; record for later maintenance, not an expansion of this milestone |
