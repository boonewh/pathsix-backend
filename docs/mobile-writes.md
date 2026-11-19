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
| First successful request | The write happens. A receipt with the exact status and JSON body is stored in the same database transaction. |
| Retry: same key, same route, same target, same body | No second write. The stored status and body come back with `Idempotent-Replayed: true`. |
| Same key with a different body, target or route | **409** `{"code": "idempotency_conflict"}`. Nothing is written. |
| Key in the wrong format | **400**. Nothing is written. |
| The write itself fails (validation, not found, permission) | Nothing is stored. Fix the request and retry; either the same key or a new one works. |
| No header | Behaviour is exactly as before. The web app doesn't send it. |

- **Scope:** keys are scoped to the signed-in user within their tenant. Two users never collide.
- **Concurrent retries:** concurrent requests with the same key run one at a time. The second waits for the first, then replays it.
- **Retention:** receipts are kept for 90 days. `scripts/prune_tombstones.py` removes older ones, and a key reused after that writes again. Clients should send a fresh key per write.
- **Replays return the stored result:** a replay returns the original response, not the record's current state. Read the record, or use the change feed, for its current state.

## Storage

`write_receipts` holds the tenant, user, key, endpoint, request hash, status and response.

- **Row security:** each user sees only their own receipts.
- **Runtime role:** may only read and insert receipts.
- **Request hash:** uses `payload_hash`, the same canonical SHA-256 that the AI connector's `ai_write_actions` uses for its request keys. It is computed over the route, the target id and the JSON body.
