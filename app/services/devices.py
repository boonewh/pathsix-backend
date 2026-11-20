"""Device sign-in for the mobile app (mobile Phase 0, item 6). Contract: docs/mobile-device-auth.md.

- A device registers with the user's email and password and receives a
  15-minute access token (JWT with `did`, `typ: device`) and a rotating refresh
  token `<user_id>.<device_uuid>.<secret>`. Only sha256(secret) is stored.
- Refresh rotates the secret. Presenting the previous secret within
  REUSE_LEEWAY of the rotation (a lost response or a duplicate request) rotates
  again; presenting it later is treated as theft and revokes the device.
- requires_auth accepts a device token only while the device exists, belongs to
  the token's user and tenant, is not revoked, and its credential marker still
  matches the user's current password hash: a password change or reset cuts off
  every device without any write.
- The offline grace period (how long the app may work without contacting the
  server) is the owner's decision and is not defined here yet.
"""
import hashlib
import os
import secrets
import time
from datetime import datetime, timedelta
from uuid import UUID

from authlib.jose import jwt
from quart import current_app

from sqlalchemy import delete

from app.models import Device, DeviceHolding, Tenant, User
from app.services.base import TenantService
from app.services.errors import RecordNotFound

ACCESS_SECONDS = 15 * 60
REUSE_LEEWAY = timedelta(minutes=2)
TOKEN_TYPE = 'device'


def refresh_days():
    raw = os.getenv('DEVICE_REFRESH_DAYS', '30')
    return int(raw) if raw.isdigit() and 1 <= int(raw) <= 365 else 30


def _sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def credential_marker(user):
    return _sha('pathsix-device-credential:' + user.password_hash)


class DeviceAuthError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def access_token(user_id, device_id, now=None):
    now = int(time.time() if now is None else now)
    payload = {'sub': user_id, 'did': str(device_id), 'typ': TOKEN_TYPE, 'iat': now,
               'exp': now + ACCESS_SECONDS}
    return jwt.encode({'alg': 'HS256'}, payload, current_app.config['SECRET_KEY']).decode('utf-8')


MAX_ID = 2_147_483_647  # PostgreSQL integer


def valid_id(raw):
    """A positive ASCII decimal id that fits a PostgreSQL integer."""
    return (isinstance(raw, str) and 1 <= len(raw) <= 10 and raw.isascii() and raw.isdigit()
            and 1 <= int(raw) <= MAX_ID)


def parse_refresh(token):
    """Split a refresh token into (user_id, device_id, secret) or raise DeviceAuthError."""
    parts = token.split('.') if isinstance(token, str) and len(token) <= 200 else []
    try:
        user_id, device_id, secret = parts
        if not valid_id(user_id) or len(secret) < 32:
            raise ValueError
        return int(user_id), UUID(device_id), secret
    except ValueError:
        raise DeviceAuthError('refresh_invalid', 'Invalid refresh token') from None


def prove_refresh(session, user, device_id, secret):
    """Before any tenant context: does this secret match the device's current or previous hash?

    A plain read under the authentication identity (users-style RLS). Unproved
    credentials all get the same generic answer from the route.
    """
    device = session.query(Device).filter(Device.id == device_id, Device.user_id == user.id,
                                          Device.tenant_id == user.tenant_id).first()
    if device is None:
        return False
    presented = _sha(secret)
    return (secrets.compare_digest(presented, device.refresh_hash)
            or bool(device.previous_refresh_hash
                    and secrets.compare_digest(presented, device.previous_refresh_hash)))


def current_account(service, user_id):
    """The user's current row, share-locked: lock order is user, then device."""
    user = service.session.query(User).populate_existing().filter(
        User.id == user_id, User.tenant_id == service.principal.tenant_id).with_for_update(read=True).first()
    tenant = None if user is None else service.session.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    if user is None or not user.is_active or tenant is None or not tenant.is_active:
        return None
    return user


def device_is_valid(session, user, device_id):
    """For requires_auth: read under the authentication identity (users-style RLS)."""
    try:
        device_id = UUID(str(device_id))
    except ValueError:
        return False
    device = session.query(Device).filter(Device.id == device_id, Device.user_id == user.id,
                                          Device.tenant_id == user.tenant_id).first()
    return (device is not None and device.revoked_at is None
            and secrets.compare_digest(device.credential_marker, credential_marker(user)))


def forget_holdings(session, tenant_id, device_ids):
    """A revoked device holds nothing the feed must track (call with the device row locked)."""
    if device_ids:
        session.execute(delete(DeviceHolding).where(DeviceHolding.tenant_id == tenant_id,
                                                    DeviceHolding.device_id.in_(list(device_ids))))


class DeviceService(TenantService):
    def _issue(self, device, user, now, *, from_previous=False):
        secret = secrets.token_urlsafe(32)
        if not from_previous:
            # The current secret becomes the previous one, with a fixed grace deadline
            # that a grace-period reissue never extends.
            device.previous_refresh_hash = device.refresh_hash
            device.previous_valid_until = now + REUSE_LEEWAY
        device.refresh_hash = _sha(secret)
        device.refreshed_at = now
        device.refresh_expires_at = now + timedelta(days=refresh_days())
        device.last_seen_at = now
        self.session.flush()
        return {
            'access_token': access_token(user.id, device.id),
            'access_expires_in': ACCESS_SECONDS,
            'refresh_token': f'{user.id}.{device.id}.{secret}',
            'refresh_expires_at': device.refresh_expires_at.isoformat() + 'Z',
            'device': self.describe(device),
        }

    @staticmethod
    def describe(device):
        return {'id': str(device.id), 'user_id': device.user_id, 'name': device.name,
                'platform': device.platform, 'created_at': device.created_at.isoformat() + 'Z',
                'last_seen_at': device.last_seen_at.isoformat() + 'Z',
                'revoked_at': device.revoked_at.isoformat() + 'Z' if device.revoked_at else None}

    def register(self, verified, name, platform):
        if verified.id != self.principal.user_id:
            raise PermissionError('Device registration identity mismatch')
        if not isinstance(name, str) or not isinstance(platform, str):
            raise ValueError('device_name (1-100 characters) and platform (ios, android or other) are required')
        name, platform = name.strip(), platform.strip().lower()
        if not 1 <= len(name) <= 100 or platform not in {'ios', 'android', 'other'}:
            raise ValueError('device_name (1-100 characters) and platform (ios, android or other) are required')
        # Serialize with deactivation (which locks the user row before revoking
        # devices) and refuse if the account or password changed since verification.
        user = current_account(self, verified.id)
        if user is None or user.password_hash != verified.password_hash:
            raise DeviceAuthError('refresh_invalid', 'Invalid credentials')
        now = datetime.utcnow()
        device = Device(tenant_id=self.principal.tenant_id, user_id=user.id, name=name, platform=platform,
                        created_at=now, refresh_hash='', refresh_expires_at=now,
                        credential_marker=credential_marker(user))
        self.session.add(device)
        self.session.flush()
        result = self._issue(device, user, now)
        device.previous_refresh_hash = device.previous_valid_until = None
        self.session.flush()
        return result

    def refresh(self, user_id, device_id, secret):
        """Call only after prove_refresh(); every check is repeated under the locks."""
        if user_id != self.principal.user_id:
            raise PermissionError('Device refresh identity mismatch')
        user = current_account(self, user_id)
        if user is None:
            raise DeviceAuthError('device_revoked', 'This device has been signed out; sign in again')
        device = self._query(Device).populate_existing().filter(
            Device.id == device_id, Device.user_id == user.id).with_for_update().first()
        if device is None:
            raise DeviceAuthError('refresh_invalid', 'Invalid refresh token')
        if device.revoked_at is not None or not secrets.compare_digest(
                device.credential_marker, credential_marker(user)):
            raise DeviceAuthError('device_revoked', 'This device has been signed out; sign in again')
        now = datetime.utcnow()
        presented = _sha(secret)
        if secrets.compare_digest(presented, device.refresh_hash):
            if device.refresh_expires_at <= now:
                raise DeviceAuthError('refresh_expired', 'Refresh token expired; sign in again')
            return self._issue(device, user, now)
        if device.previous_refresh_hash and secrets.compare_digest(presented, device.previous_refresh_hash):
            if device.previous_valid_until is not None and now <= device.previous_valid_until:
                # The client never received (or duplicated) the last rotation: issue a
                # successor from the previous secret, keeping its original deadline.
                return self._issue(device, user, now, from_previous=True)
            device.revoked_at = now  # a superseded secret used later: assume theft
            forget_holdings(self.session, device.tenant_id, [device.id])
            self.session.flush()
            raise DeviceAuthError('device_revoked', 'Refresh token reuse detected; this device was signed out')
        raise DeviceAuthError('refresh_invalid', 'Invalid refresh token')

    def _owned(self, device_id):
        try:
            device_id = UUID(str(device_id))
        except ValueError:
            raise RecordNotFound('Device not found') from None
        query = self._query(Device).filter(Device.id == device_id)
        if not self.principal.is_admin:
            # Never lock another user's device on behalf of a non-admin.
            query = query.filter(Device.user_id == self.principal.user_id)
        device = query.with_for_update().first()
        if device is None:
            raise RecordNotFound('Device not found')
        return device

    def revoke(self, device_id):
        device = self._owned(device_id)
        if device.revoked_at is None:
            device.revoked_at = datetime.utcnow()
            device.revoked_by = self.principal.user_id
            forget_holdings(self.session, device.tenant_id, [device.id])
            self.session.flush()
        return self.describe(device)

    def list(self, user_id=None):
        if user_id is not None and user_id != self.principal.user_id:
            self._require_admin()
            if self._query(User).filter(User.id == user_id).first() is None:
                raise RecordNotFound('User not found')
        target = self.principal.user_id if user_id is None else user_id
        return [self.describe(d) for d in self._query(Device).filter(Device.user_id == target)
                .order_by(Device.created_at.desc()).all()]
