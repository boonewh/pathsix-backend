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

## Mobile record routes (roadmap step 3, F-14)

Creates with the phone's own reference, and edits that merge field by field. Code: `app/services/mobile_records.py`, `app/routes/mobile_records.py`. Migration: `mobile_record_refs`. These routes are new and mobile-only. The AI connector shares the record schemas and services; nothing here changes them.

**Requirements:**
- **Device token:** without one, the route returns 403 `device_token_required`.
- **`Idempotency-Key`:** without one, the route returns 400 `idempotency_key_required`.
- **Order of checks:** body validation (stateless), then the receipt lookup, then everything else.

| Route | Body |
|---|---|
| `POST /api/mobile/records/<entity>` | `{ref, fields, parents}` |
| `PATCH /api/mobile/records/<entity>/<uuid>` | `{changes, base}` |

`<entity>` is one of `clients`, `leads`, `projects`, `accounts`, `contacts`, `interactions`.

**Responses:**

| Route | First success | Replay | Other outcomes |
|---|---|---|---|
| Create | 201 `{id, uuid, record}` | 201 `{id, uuid}` | 400 invalid; 404 parent not visible; 409 `ref_unavailable` |
| Edit | 200 `{id, uuid, record}` | 200 `{id, uuid}` | 400 invalid; 404 hidden or deleted; 409 `edit_conflict` |

`record` is in the change feed's format, or `null` when the caller cannot see the new record. A replay carries identifiers only, never content. A conflict or any failure writes no receipt.

**Fields:** only the fields in `EDITABLE` may be set. Parents, assignment, account `status` and account `opened_on` are left out because a service may ignore or rewrite them.

**Create:**
- `ref` is a canonical UUID chosen on the phone. It is stored in `mobile_record_refs`, unique per tenant (never globally), together with the record it created.
  - Row security limits each tenant to its own refs.
  - The runtime role may only SELECT and INSERT.
  - Rows are kept at least 90 days, longer than the 80-day retry deadline.
- The canonical `uuid` is generated by the server and is immutable.
- `parents` names each parent as `{"uuid": ...}` or `{"ref": ...}`, using the kinds allowed for that entity:

  | Entity | Parents |
  |---|---|
  | projects | client or lead (optional) |
  | accounts | client (required) |
  | contacts | client or lead (required) |
  | interactions | client, lead or project (required) |

- A parent the caller cannot see gives 404.

**Edit:**
- `changes` holds the fields the user changed. `base` holds the value the phone showed for each of them, in the feed's format. They must name the same fields. A missing field and an explicit `null` are different.
- The row is locked (`FOR UPDATE`) through the feed's visibility predicates, so a row the caller cannot see is never locked. The service still applies its own write rules.
- Inside a savepoint, the service applies the changes. For each field, `before` is the stored value before the change and `after` is the stored value after it.
- A field conflicts when `before != base` and `after != before`: the server moved since the phone's copy, and this edit would change it again. A value equal to what is stored after normalisation (for example, a formatted phone number) is not a conflict.
- If any field conflicts, nothing is written and the route returns:

  ```
  409 {"code": "edit_conflict", "conflicts": [{"field", "base", "mine", "theirs"}], "record": <before>}
  ```

  The whole edit waits for the user's choice (owner decision, November 20, 2026).
- Fields the phone did not change are never sent, so other people's edits to them are kept.

### Unified contacts (F-14c)

After the contacts cleanup (`docs/contacts-unified.md`), a client's or lead's person fields (`contact_person`, `email`, `phone` and the rest) and a project's `primary_contact_*` fields are stored on a contact.

- **Edits to those fields:** the mobile edit route still accepts them on the client, lead or project. It locks the person's contact row as well as the record. Both `before` and `after` are read through the model, so they come from the contact, never from the frozen backup columns.
- **Edits through the `contacts` entity:**
  - A record's primary contact, or a standalone project's person, gets 400 `edit_on_parent`. The phone edits that person on their record instead.
  - Other contacts are edited as before.
- **The feed:**
  - Contacts are sent with `project_id`/`project_uuid` and `is_primary`. Every person is sent, primary contacts included.
  - A client or lead is re-sent when its primary contact changes, and a project when the contact it points at changes. The feed reads the contact's own change marker (`SyncFeedService._changed`); no trigger touches the parent records. Migration `mobile_contacts_sync` only adds `project_id` to the contacts access fields.
  - Person-field projections ignore tombstoned contacts, because the feed reads outside the ORM tombstone filter.
- **Busy database:** a deadlock, serialization failure or lock timeout on these routes gives 503 `busy_retry` with `Retry-After: 1`. Nothing was written, and the phone retries the same request.
