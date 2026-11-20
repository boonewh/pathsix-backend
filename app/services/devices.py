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

from app.models import Device, DeviceHolding, User
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


def parse_refresh(token):
    """Split a refresh token into (user_id, device_id, secret) or raise DeviceAuthError."""
    parts = token.split('.') if isinstance(token, str) and len(token) <= 200 else []
    try:
        user_id, device_id, secret = parts
        if not user_id.isdigit() or int(user_id) < 1 or len(secret) < 32:
            raise ValueError
        return int(user_id), UUID(device_id), secret
    except ValueError:
        raise DeviceAuthError('refresh_invalid', 'Invalid refresh token') from None


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
    def _issue(self, device, user, now):
        secret = secrets.token_urlsafe(32)
        device.previous_refresh_hash = device.refresh_hash
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

    def register(self, user, name, platform):
        if user.id != self.principal.user_id:
            raise PermissionError('Device registration identity mismatch')
        name = (name or '').strip()
        platform = (platform or '').strip().lower()
        if not 1 <= len(name) <= 100 or platform not in {'ios', 'android', 'other'}:
            raise ValueError('device_name (1-100 characters) and platform (ios, android or other) are required')
        now = datetime.utcnow()
        device = Device(tenant_id=self.principal.tenant_id, user_id=user.id, name=name, platform=platform,
                        created_at=now, refresh_hash='', refresh_expires_at=now,
                        credential_marker=credential_marker(user))
        self.session.add(device)
        self.session.flush()
        result = self._issue(device, user, now)
        device.previous_refresh_hash = None
        self.session.flush()
        return result

    def refresh(self, user, device_id, secret):
        if user.id != self.principal.user_id:
            raise PermissionError('Device refresh identity mismatch')
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
            if now - device.refreshed_at <= REUSE_LEEWAY:
                # The client never received (or duplicated) the last rotation: rotate
                # again from the previous secret, which stays the previous one.
                previous = device.previous_refresh_hash
                result = self._issue(device, user, now)
                device.previous_refresh_hash = previous
                self.session.flush()
                return result
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
        device = self._query(Device).filter(Device.id == device_id).with_for_update().first()
        if device is None or (device.user_id != self.principal.user_id and not self.principal.is_admin):
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
