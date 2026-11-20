# Mobile change feed

This is how the mobile app keeps a local copy of the CRM records a user can see, and learns which ones it must drop. It is mobile Phase 0 item 4, added by migration `mobile_change_feed`. Code: `app/services/sync_feed.py`, `app/routes/sync.py` and `app/utils/sync_markers.py`.

## Request

```
GET /api/sync/changes?cursor=<token>&limit=<1-500, default 200>
Authorization: Bearer <device access token>   (docs/mobile-device-auth.md; web tokens get 403 device_token_required)
```

With no `cursor` (or an empty one), the request starts a full sync. With a cursor, always send the `cursor` from the previous response, unchanged.

## Response

```json
{"mode": "full" | "incremental",
 "changes": [{"entity": "contacts", "op": "upsert", "uuid": "…", "record": {…}},
             {"entity": "contacts", "op": "remove", "uuid": "…"}],
 "cursor": "<token>",
 "has_more": true}
```

- **Entity order:** `clients`, `leads`, `projects`, `accounts`, `contacts`, `interactions`.
- **`has_more: true`:** call again with `cursor` to continue the same round. Only the last page of a round is shorter than `limit`.
- **`has_more: false`:** the round is complete. Store `cursor` and use it for the next sync.
- **Full sync:** when you start one, discard the local copy first, then apply the full sync's pages.
- **Apply pages atomically:** apply each page and store its `cursor` together, in one local transaction. Presenting a page's `cursor` tells the server you applied that page. If a response is lost, send the previous cursor again: you get that page again, recomputed, with any removals still pending. Any older cursor of the device gets 410, which means start a full sync.
- **Upserts:** replace the local record, keyed by entity plus `uuid`. `record` holds `id` and `uuid`, and each parent appears as both `<parent>_id` and `<parent>_uuid`.
- **Datetimes:** naive ISO 8601, stored exactly as the web app stores them. Most are UTC. Interaction `contact_date` and `follow_up` are wall-clock values as the user entered them; don't shift them by a time zone.
- **Client conversion link:** clients don't include their source lead or conversion date. The web app shows that link only when the lead is visible too.
- **Removes:** delete the local record. Removes are sent only for records this device was sent and still holds. They carry no content.
- **Local identity:** key local records by entity plus `uuid`. Uniqueness is per table.
- **Repeated changes:** the same change can arrive in more than one round, so apply changes idempotently. A child can arrive before its parent; the parent follows in the same round or the next one.

## Errors

| Status | `code` | Meaning |
|---|---|---|
| 400 | `invalid_cursor` | Corrupt or forged token, a cursor issued to another user or tenant, or a cursor from before a `SYNC_FEED_GENERATION` change. Start a full sync. |
| 400 | `invalid_limit` | `limit` isn't an integer from 1 to 500. |
| 401 | — or `device_revoked` | Not signed in, or this device was signed out. |
| 403 | `device_token_required` | The feed needs a mobile device token. |
| 410 | `resync_required` | Start a full sync. Sent when: the cursor's last completed round started more than 89 days ago; the user's admin role changed; the `sync_purges` table was recreated (restore or rebuild, detected by its table identity); the cursor format changed; this device started a newer full sync; or the cursor was superseded (not this device's latest or previous page). |
| 501 | `unsupported_database` | Development SQLite only. |

## What a user sees

The feed uses the same rules as the web app:

| Entity | Visible when |
|---|---|
| Clients and leads | The user created them, is assigned to them, or is an admin. Trashed records are excluded. |
| Projects | They pass `project_access_filter`. Trashed and archived projects are excluded. |
| Accounts | Their client is visible. |
| Contacts | Their parent is visible. |
| Interactions | They pass `interaction_access_filter`. Interactions of archived projects are excluded. |

Deleted contacts, accounts and interactions (tombstones) are never sent as records.

**Removals.** A record leaves the device when any of these happens to it:

- it is deleted;
- it is reassigned, or its owner changes;
- its parent changes;
- it is archived;
- it is trashed, or a parent is trashed or reassigned away from the user;
- it is permanently deleted.

## How it works

- **Change markers.** A trigger on each of the six tables stamps the writing transaction's id into `sync_change_xid`. It also stamps `sync_access_xid` when a field that decides visibility changes; `ACCESS_FIELDS` in `app/utils/sync_markers.py` lists them. An insert never counts as an access change. The same trigger refuses any update that changes a row's `tenant_id` or `uuid`, because a device would keep the old copy.
- **Permanent deletes.** A `SECURITY DEFINER` `AFTER DELETE` trigger writes a `sync_purges` row (tenant, entity, uuid, xid, deletion time from `clock_timestamp()`). It is written in the deleting transaction and rolls back with it. The runtime role can only read these rows, so it cannot forge a removal. `scripts/prune_tombstones.py` removes rows older than 90 days. If a purged uuid reappears in the same entity (an operator reinsert) and the caller can see the replacement, its old purge record is ignored. If the caller can't see the replacement, the removal is still sent.
- **History reset.** The cursor carries the identity of the `sync_purges` table. A `pg_restore --clean` or rebuild recreates that table, which invalidates every cursor, because a restore can drop rows without leaving purge records.
- **Restores that keep the table:** point-in-time recovery or an in-place reload can rewind history without recreating that table.

### Restoring the database while devices sync

**Any restore is unsupported for an active feed until a reviewed recovery procedure exists.** This covers the backup worker's `pg_restore`, point-in-time recovery, and a logical restore into another cluster. Until then, keep the feed closed after a restore (keep the app's traffic off), and do all of the following before reopening it:

1. **Reapply the ledger and receipt permissions and verify them.** For `write_receipts` (`docs/mobile-writes.md`): runtime SELECT and INSERT, sequence USAGE, forced tenant-and-user row security, and the effective-privilege checks in `secure()` of `migrations/versions/mobile_idempotent_writes.py`. For the ledger: The backup worker dumps and restores with `--no-acl`, so a restore loses two settings: the runtime role's SELECT-only grant on `sync_purges`, and the revoked PUBLIC EXECUTE on `sync_record_purge()`. Reapply both and verify effective privileges, as `secure()` in `migrations/versions/mobile_change_feed.py` does.
2. **Rebase the markers if the restore came from another transaction history**, such as a logical restore into a different cluster. Copied markers can be ahead of the new cluster's counter: set every row's `sync_change_xid` to the current transaction and `sync_access_xid` to 0, with the marker triggers disabled for that one update, and empty `sync_purges`.
3. **Change the `SYNC_FEED_GENERATION` secret** (default `1`; any new value). Every cursor then fails with `invalid_cursor` and devices fully resync. Users stay signed in, because `SECRET_KEY` doesn't change.
- **Consistency.** Each round records `pg_snapshot_xmin(pg_current_snapshot())` before reading anything, and the next round asks for everything from that point. A transaction still running at that moment is therefore picked up next time; that's why changes can repeat.
- **Cursor.** The cursor is HMAC-signed with the app's `SECRET_KEY`, so rotating the key invalidates every cursor and devices fully resync. It carries the tenant, the user, the admin flag and the round's start time. Changing the admin flag forces a resync, because it changes visibility everywhere.
- **Retention.** Tombstones and purge records are kept for 90 days. A cursor is accepted while its lower bound, the start of its last completed round, is under 89 days old, and this is checked on every page of a round. Every removal a still-valid cursor needs is therefore retained. The one-day margin assumes no deleting transaction stays open for more than about a day; runtime requests are bounded far below that.

## Known limits

- **Device-scoped removals (Phase 0 item 6, todo 26).** `device_holdings` records what the feed has sent each device. Upserts count as held as soon as they're sent. A held record's removal is retired only when the device acknowledges the page that carried it. Removals never name records the device wasn't sent. Revoking a device deletes its holdings; the feed locks the device row first, as revocation does.
- **Responses are not cached:** they are sent with `Cache-Control: no-store`.
- **Archive owners.** Archived projects are invisible here, even to archive owners. The private archive has its own endpoint.
