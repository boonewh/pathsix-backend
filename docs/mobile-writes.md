# Retry-safe writes for the mobile app

This lets a phone retry a create or update after a lost response without creating a duplicate. It is mobile Phase 0 item 5, added by migration `mobile_idempotent_writes`. Code: `app/services/idempotency.py`.

## Contract

Send `Idempotency-Key: <key>` with a create or update. The key is 16–64 characters of letters, digits, `_` or `-`; generate a random one (for example a UUID) per intended write.

The header works on these 12 routes:

| Entity | Create | Update |
|---|---|---|
| Clients | `POST /api/clients` | `PUT /api/clients/<id>` |
| Leads | `POST /api/leads` | `PUT /api/leads/<id>` |
| Projects | `POST /api/projects` | `PUT /api/projects/<id>` |
| Accounts | `POST /api/accounts` | `PUT /api/accounts/<id>` |
| Contacts | `POST /api/contacts` | `PUT /api/contacts/<id>` |
| Interactions | `POST /api/interactions` | `PUT /api/interactions/<id>` |

What happens with the key:

| Situation | Result |
|---|---|
| First successful request | The write happens and the normal response comes back. A receipt (status and record id) is stored in the same database transaction. |
| Retry: same key, same route, same target, same body | No second write. The original status comes back with only `{"id": <record id>}` and `Idempotent-Replayed: true`. |
| Same key with a different body, target or route | **409** `{"code": "idempotency_conflict"}`. Nothing is written. |
| Key in the wrong format | **400**. Nothing is written. |
| A request with the same key is still running | **409** `{"code": "idempotency_in_progress"}` with `Retry-After: 1`. Retry with the same key. |
| The write itself fails (validation, not found, permission) | No new receipt is stored. If the key hasn't succeeded before, fix the request and retry with the same key or a new one. A key that already succeeded stays tied to that first write. |
| No header | Behaviour is exactly as before. The web app doesn't send it. |

- **Scope:** keys are scoped to the signed-in user within their tenant. Two users never collide.
- **Concurrent retries:** concurrent requests with the same key run one at a time. The second waits for the first, then replays it.
- **Retention:** receipts are kept for 90 days. `scripts/prune_tombstones.py` removes older ones, and a key reused after that writes again. Clients should send a fresh key per write.
- **Replays are acknowledgements:** a replay confirms that the original write happened and gives its record id. It never returns record content, so it can't show data the user has since lost access to. Read the record, or use the change feed, for its current state.
- **Validation runs first:** routes validate the body before checking the key, so replay and conflict apply only to requests that pass current validation. A changed body that fails validation gets the normal 400, even with a used key.
- **Retry deadline:** don't resubmit a write more than **80 days** after its first attempt, measured from that first attempt and not refreshed by retries. Past that, or after a server restore, the attempt stays **unresolved**. For an update, reading its record shows whether the desired state is present now, which is usually what matters; it can't prove the original attempt applied, because another edit may have overwritten or matched it. Reconcile a create only through a record you can positively identify as that write (for example by a value only it carries); otherwise ask the user to confirm before creating again. A similar-looking record or a missing one doesn't prove the create didn't happen, because it may have been deleted or become invisible since.
- **Database requirement:** concurrent-retry handling relies on PostgreSQL READ COMMITTED, the application's isolation level. Waiting for another request with the same key is capped at 5 seconds.
- **User ids:** receipts are keyed by numeric user id. Ids come from a sequence and aren't reused.

## Storage

`write_receipts` holds the tenant, user, key, endpoint, request hash, status and response.

- **Row security:** each user sees only their own receipts.
- **Runtime role:** may only read and insert receipts.
- **Request hash:** uses `payload_hash`, the same canonical SHA-256 that the AI connector's `ai_write_actions` uses for its request keys. It is computed over the route, the target id and the JSON body.
