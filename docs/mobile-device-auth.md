# Mobile device sign-in

This is how the mobile app signs in and stays signed in. It is mobile Phase 0 item 6, added by migration `mobile_device_auth`. Code: `app/services/devices.py`, `app/routes/devices.py`, and the device check in `requires_auth`.

The web app's login (`POST /api/auth/login`, a 30-day token) is unchanged. Retiring it needs a web-frontend change and is a separate owner decision.

## Tokens

| Token | Lifetime | Use |
|---|---|---|
| Access token (JWT, claims `sub`, `did`, `typ: "device"`) | 15 minutes | `Authorization: Bearer <access_token>` on every API call |
| Refresh token `<user_id>.<device_id>.<secret>` | 30 days from the last refresh (`DEVICE_REFRESH_DAYS`) | Only for `POST /api/auth/device/refresh` |

Store the refresh token in the iOS Keychain. Every refresh returns a **new** refresh token; discard the old one.

**Lost refresh responses:**
- **Within 2 minutes:** if a refresh response is lost, the old refresh token still works, for example on an immediate retry.
- **After 2 minutes:** the old token is treated as stolen. The device is signed out, and the user signs in again.

## Endpoints

All responses carry `Cache-Control: no-store`.

| Request | Result |
|---|---|
| `POST /api/auth/device/register` `{email, password, device_name, platform}` | `platform` is `ios`, `android` or `other`. 201: tokens, `device`, `user` and `tenant`. 401 for bad credentials. Rate-limited to 5 per minute. |
| `POST /api/auth/device/refresh` `{refresh_token}` | 200: new tokens. 401 with `code`: `refresh_invalid`, `refresh_expired` or `device_revoked`. Rate-limited to 30 per minute. |
| `POST /api/auth/device/logout` | Device token required. Revokes this device. |
| `GET /api/devices[?user_id=]` | Your devices. An admin can list a user in the same tenant. |
| `DELETE /api/devices/<id>` | Revokes. Allowed for the owner, or an admin in the same tenant; otherwise 404. |

A device token stops working when any of these happens:
- the device is revoked;
- the user's password changes or is reset;
- the user or tenant is deactivated.

It takes effect on the next request checked after the change commits; a request already admitted may finish. An API call with such a token returns 401 `{"code": "device_revoked"}`.

- **Deactivating a user** revokes all of their devices, so reactivating the account doesn't restore them.
- **Reuse detection covers one generation:** the secret just before the current one. Within 2 minutes of a rotation, presenting it rotates again: this covers a lost response or a duplicate request, and the losing response's tokens stop working. After that, presenting it revokes the device. An older or unknown secret just fails, with 401 `refresh_invalid`, and revokes nothing.
- **One refresh at a time:** the app should keep only one refresh in flight per device.

## Offline grace period

This is how long the app keeps working with no successful contact with the server. It is **not yet defined**, because the duration is the owner's decision. Once decided, the server will return it with the tokens, so the app doesn't hard-code it.

## Restoring the database

A restore can bring back revoked devices and old refresh secrets. **Before reopening after any restore, revoke every device** (set `revoked_at` on all `devices` rows, which also forces the change feed to resync), then reapply and verify the `devices` grants and row security as `secure()` in `migrations/versions/mobile_device_auth.py` does. Everyone signs in again on their phones. See also the restore gate in `docs/mobile-sync-feed.md`.

## Storage

`devices` stores the following per device:
- tenant, user, name and platform;
- the sha256 of the current and previous refresh secrets;
- refresh timing;
- a marker derived from the user's password hash;
- revocation details.

Access rules:
- **Row security:** tenant-scoped. SELECT is also allowed for the signed-in user's own rows under the authentication identity, so the per-request device check runs before the tenant principal is bound.
- **Runtime role:** may read, insert and update devices, never delete them.
- **Users:** no route deletes users, and the foreign key to users has no cascade.
