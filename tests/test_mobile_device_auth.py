"""F-6 Part A: device registration, access/refresh tokens and revocation.

The HTTP cases exercise /api/auth/device/register|refresh|logout and
/api/devices. A registered device receives a 15-minute access JWT carrying
`did` and `typ=device`, plus a rotating opaque refresh token whose sha256 is
stored on the device row. Web logins and web tokens must keep working exactly
as before.
"""
import asyncio
import hashlib
import json
import os
import time
from collections import namedtuple
from datetime import datetime, timedelta
from threading import Barrier, Thread
from uuid import UUID, uuid4

import pytest
from authlib.jose import JsonWebToken, jwt
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.models import Device, Tenant, User
from app.utils import auth_utils
from test_security_boundaries import crm as crm  # noqa: F401
from test_security_boundaries import rls_runtime as rls_runtime  # noqa: F401

Result = namedtuple('Result', 'status text headers')
PASSWORD = 'DeviceTestPass!123'
EMAIL = 'ordinary@example.test'
ADMIN_EMAIL = 'a@example.test'
FOREIGN_EMAIL = 'b@example.test'


@pytest.fixture(autouse=True)
def fresh_rate_limits():
    """Each case gets the register/refresh buckets to itself."""
    import app.utils.rate_limiter as limiter

    limiter._rate_limit_store.clear()
    yield
    limiter._rate_limit_store.clear()


@pytest.fixture
def api(crm):
    """The shared crm fixture plus a caller that accepts arbitrary tokens."""
    _, factory, app = crm

    def call(method, path, body=None, *, token=None, user=None, headers=None):
        request_headers = dict(headers or {})
        if token is None and user is not None:
            roles = ['admin'] if user in (1, 2) else []
            payload = {'sub': user, 'exp': int(time.time()) + 300, 'roles': roles}
            token = jwt.encode({'alg': 'HS256'}, payload, app.config['SECRET_KEY']).decode()
        if token is not None:
            request_headers['Authorization'] = f'Bearer {token}'

        async def run():
            response = await app.test_client().open(method=method, path=path, json=body,
                                                    headers=request_headers)
            return Result(response.status_code, await response.get_data(as_text=True),
                          {key.lower(): value for key, value in response.headers.items()})

        return asyncio.run(run())

    return call, factory, app


def body(result):
    return json.loads(result.text)


def set_password(factory, user_id, password=PASSWORD):
    with factory() as db:
        db.get(User, user_id).password_hash = auth_utils.hash_password(password)
        db.commit()


def register(call, *, email=EMAIL, password=PASSWORD, name='iPhone 17', platform='ios'):
    return call('POST', '/api/auth/device/register',
                {'email': email, 'password': password,
                 'device_name': name, 'platform': platform})


def device_rows(factory):
    with factory() as db:
        return db.query(Device).order_by(Device.created_at, Device.id).all()


def device_by_id(factory, device_id):
    with factory() as db:
        return db.get(Device, UUID(str(device_id)))


def token_claims(app, token):
    claims = JsonWebToken(['HS256']).decode(token, app.config['SECRET_KEY'])
    claims.validate()
    return claims


def device_token(app, sub, device_id, *, minutes=15, typ='device', include_typ=True):
    now = int(time.time())
    payload = {'sub': sub, 'did': str(device_id), 'iat': now,
               'exp': now + minutes * 60}
    if include_typ:
        payload['typ'] = typ
    return jwt.encode({'alg': 'HS256'}, payload, app.config['SECRET_KEY']).decode()


def refresh_parts(token):
    user_id, device_id, secret = token.split('.', 2)
    assert user_id.isdigit() and device_id and secret
    return user_id, device_id, secret


def refresh(call, token):
    return call('POST', '/api/auth/device/refresh', {'refresh_token': token})


def assert_no_store(result):
    assert result.headers['cache-control'] == 'no-store'


# Criterion 1: registration
def test_registration_creates_a_device_and_returns_working_tokens(api):
    call, factory, app = api
    set_password(factory, 3)
    before = len(device_rows(factory))

    result = register(call)

    assert result.status == 201, result.text
    assert_no_store(result)
    payload = body(result)
    assert set(payload) >= {'access_token', 'access_expires_in', 'refresh_token',
                            'refresh_expires_at', 'device', 'user', 'tenant'}
    assert payload['access_expires_in'] == 900
    assert payload['device']['name'] == 'iPhone 17'
    assert payload['device']['platform'] == 'ios'
    assert payload['user']['id'] == 3 and payload['tenant']['id'] == 1
    assert 'password' not in json.dumps(payload)
    assert 'refresh_hash' not in json.dumps(payload)

    device_id = payload['device']['id']
    device = device_by_id(factory, device_id)
    assert device is not None and device.tenant_id == 1 and device.user_id == 3
    assert device.revoked_at is None
    assert len(device_rows(factory)) == before + 1
    user_id, token_device_id, secret = refresh_parts(payload['refresh_token'])
    assert (user_id, token_device_id) == ('3', device_id)
    expected_hash = hashlib.sha256(secret.encode()).hexdigest()
    assert device.refresh_hash == expected_hash and device.previous_refresh_hash is None

    claims = token_claims(app, payload['access_token'])
    assert claims['sub'] == 3 and claims['did'] == device_id
    assert claims['typ'] == 'device'
    assert claims['exp'] - claims['iat'] == 900
    assert call('GET', '/api/me', token=payload['access_token']).status == 200

    rotated = refresh(call, payload['refresh_token'])
    assert rotated.status == 200, rotated.text
    assert body(rotated)['refresh_token'] != payload['refresh_token']


@pytest.mark.parametrize('email,password,expected', [
    (EMAIL, 'WrongTestPass!123', 401),
    ('missing@example.test', PASSWORD, 401),
    ('', PASSWORD, 400),
    (EMAIL, '', 400),
])
def test_bad_credentials_return_no_device(api, email, password, expected):
    call, factory, _ = api
    set_password(factory, 3)
    before = len(device_rows(factory))

    result = register(call, email=email, password=password)

    assert result.status == expected, result.text
    assert_no_store(result)
    assert len(device_rows(factory)) == before


@pytest.mark.parametrize('disabled', ['user', 'tenant'])
def test_inactive_user_or_tenant_cannot_register(api, disabled):
    call, factory, _ = api
    set_password(factory, 3)
    before = len(device_rows(factory))
    with factory() as db:
        target = db.get(User if disabled == 'user' else Tenant, 3 if disabled == 'user' else 1)
        target.is_active = False
        db.commit()

    result = register(call)

    assert result.status == 401, result.text
    assert_no_store(result)
    assert len(device_rows(factory)) == before


def test_registration_is_rate_limited(api):
    call, factory, _ = api
    set_password(factory, 3)
    before = len(device_rows(factory))

    bad = {'email': EMAIL, 'password': 'WrongTestPass!123',
           'device_name': 'iPhone', 'platform': 'ios'}
    statuses = [call('POST', '/api/auth/device/register', bad).status for _ in range(5)]
    limited = call('POST', '/api/auth/device/register', bad)

    assert statuses == [401] * 5
    assert limited.status == 429, limited.text
    assert_no_store(limited)  # the rate limiter returns before the route body
    assert body(limited)['retry_after'] > 0
    assert len(device_rows(factory)) == before


@pytest.mark.parametrize('method,path', [
    ('GET', '/api/devices'),
    ('POST', '/api/auth/device/logout'),
])
def test_device_endpoints_no_store_on_auth_early_return(api, method, path):
    call, _, _ = api

    result = call(method, path)

    assert result.status == 401, result.text
    assert_no_store(result)


@pytest.mark.parametrize('change', ['deactivate', 'password'])
def test_registration_rechecks_state_after_verification(api, monkeypatch, change):
    """The password/active check at verification is repeated under the user lock."""
    call, factory, _ = api
    from app.routes import devices as device_routes

    set_password(factory, 3)
    before = len(device_rows(factory))
    original = device_routes.DeviceService.register

    def changed_after_verification(service, verified, name, platform):
        with factory() as db:
            user = db.get(User, verified.id)
            if change == 'deactivate':
                user.is_active = False
            else:
                user.password_hash = auth_utils.hash_password('ChangedPass!123')
            db.commit()
        return original(service, verified, name, platform)

    monkeypatch.setattr(device_routes.DeviceService, 'register', changed_after_verification)

    result = register(call)

    assert result.status == 401, result.text
    assert_no_store(result)
    assert len(device_rows(factory)) == before


def test_registration_waiting_on_deactivation_cannot_revive_after_reactivation(api):
    """A share lock serializes registration with deactivation; reactivation adds no device."""
    call, factory, _ = api
    set_password(factory, 3)
    before = len(device_rows(factory))
    outcome = {}
    thread = None
    blocker = factory()
    try:
        blocker.execute(text('SELECT id FROM users WHERE id=3 FOR UPDATE'))
        blocker.execute(text('UPDATE users SET is_active=false WHERE id=3'))
        blocker.flush()  # still uncommitted: verification sees the committed active row
        thread = Thread(target=lambda: outcome.update(result=register(call)), daemon=True)
        thread.start()
        time.sleep(0.75)
        assert thread.is_alive(), 'registration did not wait for the user row lock'
        blocker.commit()
        thread.join(timeout=20)
    finally:
        if thread is not None and thread.is_alive():
            thread.join(timeout=20)
        blocker.close()

    assert 'result' in outcome, 'registration never returned'
    assert outcome['result'].status == 401, outcome['result'].text
    assert_no_store(outcome['result'])
    with factory() as db:
        db.get(User, 3).is_active = True
        db.commit()
    assert len(device_rows(factory)) == before


# Criterion 2: access-token expiry
def test_access_token_expires_after_fifteen_minutes(api):
    call, factory, app = api
    set_password(factory, 3)
    registered = register(call)
    assert registered.status == 201, registered.text
    payload = body(registered)

    claims = token_claims(app, payload['access_token'])
    assert claims['exp'] - claims['iat'] == 900
    expired = device_token(app, 3, payload['device']['id'], minutes=-1)

    result = call('GET', '/api/me', token=expired)

    assert result.status == 401, result.text


# Criterion 3: refresh rotation and expiry
def test_refresh_rotates_the_secret(api):
    call, factory, _ = api
    set_password(factory, 3)
    registered = register(call)
    assert registered.status == 201, registered.text
    original = body(registered)
    device = device_by_id(factory, original['device']['id'])
    original_hash = device.refresh_hash

    rotated = refresh(call, original['refresh_token'])

    assert rotated.status == 200, rotated.text
    assert_no_store(rotated)
    payload = body(rotated)
    assert payload['refresh_token'] != original['refresh_token']
    assert payload['access_expires_in'] == 900
    device = device_by_id(factory, original['device']['id'])
    assert device.refresh_hash != original_hash
    assert device.previous_refresh_hash == original_hash


def test_previous_secret_rotates_again_within_a_fixed_reuse_deadline(api):
    import app.services.devices as device_service

    call, factory, _ = api
    set_password(factory, 3)
    first = body(register(call))
    original_hash = device_by_id(factory, first['device']['id']).refresh_hash
    second = body(refresh(call, first['refresh_token']))
    device = device_by_id(factory, first['device']['id'])
    deadline = device.previous_valid_until
    previous_hash = device.previous_refresh_hash
    assert deadline is not None and previous_hash == original_hash
    assert datetime.utcnow() < deadline < datetime.utcnow() + device_service.REUSE_LEEWAY + timedelta(seconds=1)

    retried = refresh(call, first['refresh_token'])

    assert retried.status == 200, retried.text
    third = body(retried)
    assert third['refresh_token'] not in (first['refresh_token'], second['refresh_token'])
    device = device_by_id(factory, first['device']['id'])
    assert device.revoked_at is None
    # A grace reissue does not extend the previous secret's original deadline.
    assert device.previous_valid_until == deadline
    assert device.previous_refresh_hash == original_hash
    # The lost rotation's secret is no longer either stored hash.
    assert refresh(call, second['refresh_token']).status == 401


def test_reusing_the_previous_secret_after_the_deadline_revokes_the_device(api):
    call, factory, _ = api
    set_password(factory, 3)
    first = body(register(call))
    second = body(refresh(call, first['refresh_token']))
    with factory() as db:
        device = db.get(Device, UUID(first['device']['id']))
        device.previous_valid_until = datetime.utcnow() - timedelta(seconds=1)
        db.commit()

    stolen = refresh(call, first['refresh_token'])

    assert stolen.status == 401, stolen.text
    assert body(stolen)['code'] == 'device_revoked'
    assert_no_store(stolen)
    device = device_by_id(factory, first['device']['id'])
    assert device.revoked_at is not None
    assert call('GET', '/api/me', token=first['access_token']).status == 401
    assert call('GET', '/api/me', token=second['access_token']).status == 401
    assert refresh(call, second['refresh_token']).status == 401


def test_concurrent_refreshes_with_one_secret_have_exactly_one_winner(api, monkeypatch):
    import app.services.devices as device_service

    # The documented 2-minute lost-response retry deliberately lets a second
    # use of the previous secret succeed. Disable that window to check the row
    # lock itself: with a single refresh in flight, only one rotation wins.
    monkeypatch.setattr(device_service, 'REUSE_LEEWAY', timedelta(0))
    call, factory, _ = api
    set_password(factory, 3)
    registered = body(register(call))
    token = registered['refresh_token']
    barrier = Barrier(2)
    results = {}

    def worker(index):
        try:
            barrier.wait(timeout=10)
            results[index] = refresh(call, token)
        except BaseException as error:  # asserted below
            results[index] = error

    threads = [Thread(target=worker, args=(index,), daemon=True) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert all(not thread.is_alive() for thread in threads), 'a concurrent refresh hung'
    assert all(isinstance(result, Result) for result in results.values()), results

    responses = list(results.values())
    assert sorted(result.status for result in responses) == [200, 401]
    winner = next(result for result in responses if result.status == 200)
    loser = next(result for result in responses if result.status == 401)
    assert body(loser)['code'] == 'device_revoked'
    assert body(winner)['refresh_token'] != token


def test_expired_refresh_returns_401_without_rotating(api):
    call, factory, _ = api
    set_password(factory, 3)
    registered = body(register(call))
    with factory() as db:
        db.get(Device, registered['device']['id']).refresh_expires_at = \
            datetime.utcnow() - timedelta(seconds=1)
        db.commit()
    device = device_by_id(factory, registered['device']['id'])
    before = (device.refresh_hash, device.previous_refresh_hash, device.revoked_at)

    result = refresh(call, registered['refresh_token'])

    assert result.status == 401, result.text
    assert body(result)['code'] == 'refresh_expired'
    assert_no_store(result)
    device = device_by_id(factory, registered['device']['id'])
    assert (device.refresh_hash, device.previous_refresh_hash, device.revoked_at) == before


@pytest.mark.parametrize('case', [
    'unknown_user', 'inactive_user', 'wrong_device', 'wrong_secret',
    'zero_user', 'overflow_user', 'long_user',
])
def test_unproved_refresh_tokens_are_indistinguishable(api, monkeypatch, case):
    """Every unproved token gets the same 401 before the locked service path."""
    import app.services.devices as device_service

    call, factory, _ = api
    set_password(factory, 3)
    registered = body(register(call))
    user_id, device_id, secret = refresh_parts(registered['refresh_token'])

    if case == 'unknown_user':
        token = f'999999.{device_id}.{secret}'
    elif case == 'inactive_user':
        with factory() as db:
            db.get(User, 3).is_active = False
            db.commit()
        token = registered['refresh_token']
    elif case == 'wrong_device':
        token = f'{user_id}.{uuid4()}.{secret}'
    elif case == 'wrong_secret':
        token = f'{user_id}.{device_id}.{"w" * 40}'
    elif case == 'zero_user':
        token = f'0.{device_id}.{secret}'
    elif case == 'overflow_user':
        token = f'2147483648.{device_id}.{secret}'
    else:
        token = f'99999999999.{device_id}.{secret}'

    def locked_path(*_args, **_kwargs):
        pytest.fail('an unproved refresh reached the locked service path')

    monkeypatch.setattr(device_service, 'current_account', locked_path)
    result = refresh(call, token)

    assert result.status == 401, result.text
    assert body(result) == {'code': 'refresh_invalid', 'error': 'Invalid refresh token'}
    assert_no_store(result)
    assert len(device_rows(factory)) == 1
    assert device_by_id(factory, registered['device']['id']).revoked_at is None


@pytest.mark.parametrize('raw', ['0', '2147483648', '99999999999'])
def test_device_list_rejects_out_of_range_user_ids(api, raw):
    call, factory, _ = api
    set_password(factory, 1)

    result = call('GET', f'/api/devices?user_id={raw}', user=1)

    assert result.status == 400, result.text
    assert_no_store(result)


def test_device_list_accepts_the_largest_valid_user_id(api):
    call, factory, _ = api
    set_password(factory, 1)

    result = call('GET', '/api/devices?user_id=2147483647', user=1)

    assert result.status == 404, result.text  # a valid id that has no such user
    assert_no_store(result)


def test_password_reset_between_proof_and_refresh_lock_is_caught(api, monkeypatch):
    """The secret proves first; the locked recheck sees the new password hash."""
    import app.services.devices as device_service

    call, factory, _ = api
    set_password(factory, 3)
    registered = body(register(call))
    original = device_service.current_account

    def reset_then_recheck(service, user_id):
        with factory() as db:
            db.get(User, user_id).password_hash = auth_utils.hash_password('ResetPass!123')
            db.commit()
        return original(service, user_id)

    monkeypatch.setattr(device_service, 'current_account', reset_then_recheck)

    result = refresh(call, registered['refresh_token'])

    assert result.status == 401, result.text
    assert body(result)['code'] == 'device_revoked', result.text
    assert_no_store(result)


# Criterion 4: revocation
@pytest.mark.parametrize('how', ['logout', 'owner_delete', 'admin_delete'])
def test_revocation_kills_access_and_refresh_tokens(api, how):
    call, factory, _ = api
    set_password(factory, 3)
    registered = body(register(call))
    device_id = registered['device']['id']
    access = registered['access_token']
    refresh_token = registered['refresh_token']

    if how == 'logout':
        result = call('POST', '/api/auth/device/logout', token=access)
    elif how == 'owner_delete':
        result = call('DELETE', f'/api/devices/{device_id}', user=3)
    else:
        result = call('DELETE', f'/api/devices/{device_id}', user=1)

    assert result.status in (200, 204), result.text
    assert_no_store(result)
    device = device_by_id(factory, device_id)
    assert device.revoked_at is not None
    assert device.revoked_by is not None
    assert call('GET', '/api/me', token=access).status == 401
    assert refresh(call, refresh_token).status == 401


def test_web_token_cannot_use_the_logout_or_device_routes(api):
    call, factory, app = api
    set_password(factory, 3)
    registered = body(register(call))

    logout = call('POST', '/api/auth/device/logout', user=3)
    listed = call('GET', '/api/devices', user=3)

    assert logout.status == 400, logout.text
    assert body(logout)['code'] == 'device_token_required'
    assert device_by_id(factory, registered['device']['id']).revoked_at is None
    assert listed.status == 200, listed.text


def test_non_admin_cannot_revoke_another_users_device(api):
    call, factory, _ = api
    set_password(factory, 3)
    with factory() as db:
        db.add(User(id=4, tenant_id=1, email='fourth@example.test',
                    password_hash=auth_utils.hash_password(PASSWORD)))
        db.commit()
    registered = body(register(call))

    result = call('DELETE', f"/api/devices/{registered['device']['id']}", user=4)

    assert result.status == 404, result.text
    assert device_by_id(factory, registered['device']['id']).revoked_at is None


def test_cross_tenant_device_id_is_not_visible(api):
    call, factory, _ = api
    set_password(factory, 2, 'OtherTenantPass!123')
    registered = body(register(call, email=FOREIGN_EMAIL, password='OtherTenantPass!123'))

    result = call('DELETE', f"/api/devices/{registered['device']['id']}", user=1)
    listed = call('GET', '/api/devices', user=1)

    assert result.status == 404, result.text
    assert listed.status == 200, listed.text
    assert registered['device']['id'] not in listed.text
    assert device_by_id(factory, registered['device']['id']).revoked_at is None


def test_device_list_is_personal_and_admin_can_scope_by_user(api):
    call, factory, _ = api
    set_password(factory, 3, 'ListPass!123')
    set_password(factory, 1)
    own = body(register(call, email=EMAIL, password='ListPass!123', name='Own phone'))
    admin_device = body(register(call, email=ADMIN_EMAIL, name='Admin phone'))

    mine = call('GET', '/api/devices', user=3)
    assert mine.status == 200, mine.text
    assert [item['id'] for item in body(mine)] == [own['device']['id']]

    admins = call('GET', '/api/devices', user=1)
    assert admins.status == 200, admins.text
    assert [item['id'] for item in body(admins)] == [admin_device['device']['id']]

    scoped = call('GET', '/api/devices?user_id=3', user=1)
    assert scoped.status == 200, scoped.text
    assert [item['id'] for item in body(scoped)] == [own['device']['id']]

    denied = call('GET', '/api/devices?user_id=1', user=3)
    assert denied.status == 403, denied.text


# Criteria 5 and the deactivation follow-up
@pytest.mark.parametrize('how', ['change', 'reset'])
def test_password_change_and_reset_invalidate_every_device(api, how):
    call, factory, app = api
    before_password = 'BeforePass!123'
    set_password(factory, 3, before_password)
    first = body(register(call, password=before_password, name='Phone one'))
    second = body(register(call, password=before_password, name='Phone two'))

    if how == 'change':
        result = call('POST', '/api/change-password',
                      {'current_password': before_password,
                       'new_password': PASSWORD}, user=3)
    else:
        from itsdangerous import URLSafeTimedSerializer
        reset_token = URLSafeTimedSerializer(app.config['SECRET_KEY']).dumps(
            EMAIL, salt='password-reset')
        result = call('POST', '/api/reset-password',
                      {'token': reset_token, 'password': PASSWORD})
    assert result.status == 200, result.text

    for registered in (first, second):
        assert call('GET', '/api/me', token=registered['access_token']).status == 401
        assert refresh(call, registered['refresh_token']).status == 401
    for registered in (first, second):
        device = device_by_id(factory, registered['device']['id'])
        assert device.revoked_at is None  # the marker, not a write, cuts them off


def test_deactivation_revokes_devices_and_reactivation_does_not_restore(api):
    call, factory, _ = api
    set_password(factory, 3)
    registered = body(register(call))
    device_id = UUID(registered['device']['id'])

    deactivated = call('PUT', '/api/users/3/toggle-active', user=1)
    assert deactivated.status == 200, deactivated.text
    with factory() as db:
        assert db.get(User, 3).is_active is False
        revoked = db.get(Device, device_id)
        assert revoked.revoked_at is not None and revoked.revoked_by == 1
    assert call('GET', '/api/me', token=registered['access_token']).status == 401
    assert refresh(call, registered['refresh_token']).status == 401

    reactivated = call('PUT', '/api/users/3/toggle-active', user=1)
    assert reactivated.status == 200, reactivated.text
    with factory() as db:
        assert db.get(User, 3).is_active is True
        assert db.get(Device, device_id).revoked_at is not None
    denied = call('GET', '/api/me', token=registered['access_token'])
    assert denied.status == 401
    assert body(denied)['code'] == 'device_revoked'
    assert refresh(call, registered['refresh_token']).status == 401


# Criterion 6: web unchanged
def test_web_login_and_web_tokens_are_unchanged(api):
    call, factory, app = api
    set_password(factory, 1)

    result = call('POST', '/api/login',
                  {'email': ADMIN_EMAIL, 'password': PASSWORD})

    assert result.status == 200, result.text
    assert_no_store(result)
    payload = body(result)
    assert payload['token'] and payload['user']['id'] == 1 and payload['tenant']['id'] == 1
    assert 'access_token' not in payload and 'refresh_token' not in payload
    claims = token_claims(app, payload['token'])
    assert 'did' not in claims and claims.get('typ') != 'device'
    assert claims['exp'] - int(time.time()) >= 29 * 86400
    assert call('GET', '/api/me', token=payload['token']).status == 200


# Criterion 7: runtime role and isolation
@pytest.mark.parametrize('claim', [
    {'did': None},
    {'typ': 'web'},
    {'typ': None},
])
def test_device_claim_shapes_are_not_accepted_as_device_tokens(api, claim):
    call, factory, app = api
    set_password(factory, 3)
    registered = body(register(call))
    payload = {'sub': 3, 'did': registered['device']['id'], 'typ': 'device',
               'iat': int(time.time()), 'exp': int(time.time()) + 300}
    payload.update(claim)
    payload = {key: value for key, value in payload.items() if value is not None}
    token = jwt.encode({'alg': 'HS256'}, payload, app.config['SECRET_KEY']).decode()

    result = call('GET', '/api/me', token=token)

    assert result.status == 401, result.text


def test_device_token_cannot_impersonate_another_user_or_tenant(api):
    call, factory, app = api
    set_password(factory, 3)
    own = body(register(call))
    set_password(factory, 1)
    foreign = body(register(call, email=ADMIN_EMAIL))

    other_user = device_token(app, 3, foreign['device']['id'])
    other_tenant = device_token(app, 2, own['device']['id'])

    assert call('GET', '/api/me', token=other_user).status == 401
    assert call('GET', '/api/me', token=other_tenant).status == 401


def test_runtime_role_isolation_privileges_and_contract(rls_runtime):
    from app.services.database_context import auth_lookup, bind_principal
    from app.services.principal import Principal
    from ops.isolation_contract import TABLES, verify_isolation_contract

    assert 'devices' in TABLES
    factory, admin, schema = rls_runtime
    ids = {}
    now = datetime.utcnow()
    with admin() as db:
        for tenant, user, name in ((1, 1, 'one'), (1, 3, 'three'), (2, 2, 'two')):
            device = Device(id=uuid4(), tenant_id=tenant, user_id=user, name=name,
                            platform='ios', created_at=now, last_seen_at=now,
                            refresh_hash='a' * 64, refreshed_at=now,
                            refresh_expires_at=now + timedelta(days=30),
                            credential_marker='c' * 64)
            db.add(device)
            ids[(tenant, user)] = device.id
        db.commit()

    with factory() as db:
        bind_principal(db, Principal(1, 1, frozenset({'admin'})))
        assert set(db.execute(text('SELECT id FROM devices')).scalars()) == {
            ids[(1, 1)], ids[(1, 3)]}
        with db.begin_nested() as savepoint:
            with pytest.raises(DBAPIError) as denied:
                db.execute(text('DELETE FROM devices'))
            assert denied.value.orig.pgcode == '42501'
            savepoint.rollback()
        db.rollback()

    with factory() as db:
        auth_lookup(db, user_id=3)
        assert db.execute(text('SELECT id FROM devices')).scalars().all() == [ids[(1, 3)]]
        db.rollback()

    with admin() as db:
        db.execute(text(f'SET LOCAL search_path="{schema}"'))
        checked = verify_isolation_contract(db.connection(), schema, os.environ['SECURITY_TEST_ROLE'])
    assert checked['role_restricted'] and checked['tables'] == len(TABLES)
