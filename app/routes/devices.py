"""Mobile device sign-in, refresh, sign-out and device management (docs/mobile-device-auth.md)."""
from quart import Blueprint, jsonify, request
from sqlalchemy.orm import joinedload

from app.database import SessionLocal
from app.models import User
from app.services.database_context import auth_lookup
from app.services.devices import DeviceAuthError, DeviceService, parse_refresh
from app.services.errors import RecordNotFound
from app.services.principal import Principal
from app.utils.auth_utils import requires_auth, verify_password
from app.utils.rate_limiter import rate_limit

devices_bp = Blueprint('devices', __name__)


def _private(body, status=200):
    response = jsonify(body)
    response.status_code = status
    response.headers['Cache-Control'] = 'no-store'
    return response


def _load_user(*, email=None, user_id=None):
    """Authentication lookup in its own session, as login does; returns a detached user."""
    with SessionLocal() as session:
        auth_lookup(session, email=email) if email is not None else auth_lookup(session, user_id=user_id)
        query = session.query(User).options(joinedload(User.tenant), joinedload(User.roles))
        user = (query.filter_by(email=email) if email is not None else query.filter_by(id=user_id)).first()
        if user is None or not user.is_active or not user.tenant or not user.tenant.is_active:
            return None
        session.expunge(user)
        return user


def _principal(user):
    principal = Principal(user.id, user.tenant_id, frozenset(role.name for role in user.roles))
    request.principal = principal  # services check the bound identity matches the request
    return principal


def _identity(user):
    body = {'user': {'id': user.id, 'email': user.email, 'roles': [r.name for r in user.roles],
                     'tenant_id': user.tenant_id}}
    if user.tenant:
        body['tenant'] = {'id': user.tenant.id, 'name': user.tenant.name, 'slug': user.tenant.slug,
                          'config': user.tenant.config}
    return body


@devices_bp.route('/api/auth/device/register', methods=['POST'])
@rate_limit(max_attempts=5, window_seconds=60)
async def register():
    data = await request.get_json(silent=True)
    if not isinstance(data, dict):
        return _private({'error': 'Invalid request body'}, 400)
    email = str(data.get('email') or '').lower().strip()
    password = data.get('password')
    if not email or not isinstance(password, str) or not password:
        return _private({'error': 'Missing credentials'}, 400)
    user = _load_user(email=email)
    if user is None or not verify_password(password, user.password_hash):
        return _private({'error': 'Invalid credentials'}, 401)
    with SessionLocal() as session:
        try:
            result = DeviceService(session, _principal(user)).register(
                user, data.get('device_name'), data.get('platform'))
            session.commit()
        except ValueError as exc:
            session.rollback()
            return _private({'error': str(exc)}, 400)
    return _private({**result, **_identity(user)}, 201)


@devices_bp.route('/api/auth/device/refresh', methods=['POST'])
@rate_limit(max_attempts=30, window_seconds=60)
async def refresh():
    data = await request.get_json(silent=True)
    try:
        user_id, device_id, secret = parse_refresh((data or {}).get('refresh_token') if isinstance(data, dict) else None)
    except DeviceAuthError as exc:
        return _private({'code': exc.code, 'error': str(exc)}, 401)
    user = _load_user(user_id=user_id)
    if user is None:
        return _private({'code': 'device_revoked', 'error': 'This account cannot sign in'}, 401)
    with SessionLocal() as session:
        try:
            result = DeviceService(session, _principal(user)).refresh(user, device_id, secret)
            session.commit()
        except DeviceAuthError as exc:
            session.commit()  # keeps a reuse revocation; other failures changed nothing
            return _private({'code': exc.code, 'error': str(exc)}, 401)
    return _private(result)


@devices_bp.route('/api/auth/device/logout', methods=['POST'])
@requires_auth()
async def logout():
    if not request.device_id:
        return _private({'code': 'device_token_required', 'error': 'Sign out needs a device token'}, 400)
    with SessionLocal() as session:
        result = DeviceService(session, request.principal).revoke(request.device_id)
        session.commit()
    return _private(result)


@devices_bp.route('/api/devices', methods=['GET'])
@requires_auth()
async def list_devices():
    raw = request.args.get('user_id')
    if raw is not None and (not raw.isdigit() or int(raw) < 1):
        return _private({'error': 'Invalid user_id'}, 400)
    with SessionLocal() as session:
        try:
            return _private(DeviceService(session, request.principal).list(int(raw) if raw else None))
        except PermissionError as exc:
            return _private({'error': str(exc)}, 403)
        except RecordNotFound as exc:
            return _private({'error': str(exc)}, 404)
        finally:
            session.rollback()


@devices_bp.route('/api/devices/<device_id>', methods=['DELETE'])
@requires_auth()
async def revoke_device(device_id):
    with SessionLocal() as session:
        try:
            result = DeviceService(session, request.principal).revoke(device_id)
            session.commit()
            return _private(result)
        except RecordNotFound as exc:
            return _private({'error': str(exc)}, 404)
