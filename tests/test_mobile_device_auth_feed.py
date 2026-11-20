"""F-6 Part B: device-bound cursors, scoped removals and review-round behaviour.

A removal only reaches a device that was sent the record earlier; newly visible
rows are still upserted for devices that never held them. The cursor is bound to
the device and its sync generation, and one page is outstanding: re-presenting
the acknowledged cursor recomputes a lost page, while an older acknowledged
cursor has been superseded and forces a resync. HTTP cases use device tokens;
direct SyncFeedService cases in test_mobile_sync_feed.py keep device_id=None.
"""
import json
import time
from collections import namedtuple
from datetime import datetime, timedelta
from urllib.parse import urlencode
from uuid import UUID, uuid4

import pytest
from authlib.jose import jwt
from sqlalchemy import text

from app.models import Device, DeviceHolding, Tenant, User
from app.services.principal import Principal
from app.services.purge import PurgeService
from app.utils import auth_utils
from test_mobile_device_auth import (ADMIN_EMAIL, PASSWORD, assert_no_store, body,
                                      device_by_id, refresh, register, set_password)
from test_security_boundaries import crm as crm  # noqa: F401

Result = namedtuple('Result', 'status text headers')
CLIENT_ID = 2000
OTHER_CLIENT_ID = 2001
UNHELD_CLIENT_ID = 1998
# Explicit fixture ids do not move sequences; keep this tree's project-call id
# clear of the client ids used by other trees in the same test.
PROJECT_CALL_GAP = 10000


@pytest.fixture(autouse=True)
def fresh_rate_limits():
    """Register/refresh buckets are per case, not shared through the whole run."""
    import app.utils.rate_limiter as limiter

    limiter._rate_limit_store.clear()
    yield
    limiter._rate_limit_store.clear()


@pytest.fixture
def api(crm):
    """The shared crm schema plus a caller that accepts arbitrary tokens."""
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

        return asyncio_run(run())

    return call, factory, app


def asyncio_run(coroutine):
    import asyncio

    return asyncio.run(coroutine)


def feed(call, token, cursor=None, limit=None):
    params = {}
    if cursor is not None:
        params['cursor'] = cursor
    if limit is not None:
        params['limit'] = str(limit)
    path = '/api/sync/changes' + ('?' + urlencode(params) if params else '')
    result = call('GET', path, token=token)
    return result.status, body(result) if result.text else {}


def drain(call, token, cursor=None, limit=200, max_pages=100):
    changes = []
    for _ in range(max_pages):
        status, page = feed(call, token, cursor, limit)
        assert status == 200, page
        changes.extend(page['changes'])
        cursor = page['cursor']
        if not page['has_more']:
            return changes, cursor
    pytest.fail('the feed never finished paging')


def add_user(factory, user_id, email, tenant_id=1):
    with factory() as db:
        db.add(User(id=user_id, tenant_id=tenant_id, email=email,
                    password_hash=auth_utils.hash_password(PASSWORD)))
        db.commit()


def uuid_of(factory, table, record_id):
    with factory() as db:
        return str(db.execute(text(f'SELECT uuid FROM {table} WHERE id=:id'),
                              {'id': record_id}).scalar_one())


def project_call_id(client_id):
    return client_id + PROJECT_CALL_GAP


def seed_client_tree(factory, client_id, owner=3):
    """A client with inherited children, visible to `owner` only via assignment.

    `created_by=1` keeps the ordinary user's visibility dependent on
    `assigned_to`; the project is unassigned so it inherits the client rather
    than staying visible through a direct assignment.
    """
    call_id = project_call_id(client_id)
    with factory.begin() as c:
        c.execute(text(
            'INSERT INTO clients (id, tenant_id, created_by, assigned_to, name) '
            'VALUES (:id, 1, 1, :owner, :name)'),
            {'id': client_id, 'owner': owner, 'name': f'Tree {client_id}'})
        c.execute(text(
            'INSERT INTO accounts (id, tenant_id, client_id, account_number) '
            'VALUES (:id, 1, :client, :number)'),
            {'id': client_id, 'client': client_id, 'number': f'A-{client_id}'})
        c.execute(text(
            'INSERT INTO contacts (id, tenant_id, client_id, first_name) '
            'VALUES (:id, 1, :client, :name)'),
            {'id': client_id, 'client': client_id, 'name': f'C-{client_id}'})
        c.execute(text(
            "INSERT INTO interactions (id, tenant_id, client_id, summary, followup_status) "
            "VALUES (:id, 1, :client, :summary, 'pending')"),
            {'id': client_id, 'client': client_id, 'summary': f'I-{client_id}'})
        c.execute(text(
            'INSERT INTO projects (id, tenant_id, created_by, client_id, '
            "project_name, project_status) VALUES (:id, 1, 1, :client, :name, 'pending')"),
            {'id': client_id, 'client': client_id, 'name': f'P-{client_id}'})
        c.execute(text(
            "INSERT INTO interactions (id, tenant_id, project_id, summary, followup_status) "
            "VALUES (:id, 1, :project, :summary, 'pending')"),
            {'id': call_id, 'project': client_id, 'summary': f'PI-{client_id}'})
    return {
        (table, uuid_of(factory, table, record_id))
        for table, record_id in (('clients', client_id), ('accounts', client_id),
                                 ('contacts', client_id), ('interactions', client_id),
                                 ('projects', client_id), ('interactions', call_id))
    }


def pairs(changes):
    return {(change['entity'], change['uuid']) for change in changes}


def removals(changes):
    return pairs(change for change in changes if change['op'] == 'remove')


def upserts(changes):
    return pairs(change for change in changes if change['op'] == 'upsert')


def holdings(factory, device_id):
    with factory() as db:
        return {(row.entity, str(row.record_uuid))
                for row in db.query(DeviceHolding).filter(
                    DeviceHolding.device_id == UUID(str(device_id))).all()}


def device_state(factory, device_id):
    with factory() as db:
        device = db.get(Device, UUID(str(device_id)))
        return (device.sync_generation, device.sync_acked_seq,
                device.sync_pending_seq, tuple(device.sync_pending_removals or ()))


def mutate(factory, call, how, client_id=CLIENT_ID):
    if how == 'reassign':
        result = call('PUT', f'/api/clients/{client_id}/assign', {'assigned_to': 1}, user=1)
    elif how == 'reassign_to_three':
        result = call('PUT', f'/api/clients/{client_id}/assign', {'assigned_to': 3}, user=1)
    elif how == 'reassign_to_four':
        result = call('PUT', f'/api/clients/{client_id}/assign', {'assigned_to': 4}, user=1)
    elif how == 'trash':
        result = call('DELETE', f'/api/clients/{client_id}', user=1)
    elif how == 'restore':
        result = call('PUT', f'/api/clients/{client_id}/restore', user=1)
    elif how == 'archive':
        result = None
        with factory.begin() as c:
            c.execute(text('UPDATE projects SET archived_at=:at, archived_by=1 WHERE id=:id'),
                      {'at': datetime.utcnow(), 'id': client_id})
    elif how == 'purge':
        result = None
        targets = {'clients': [client_id], 'accounts': [client_id], 'contacts': [client_id],
                   'projects': [client_id], 'interactions': [client_id, project_call_id(client_id)]}
        now = datetime.utcnow()
        with factory.begin() as c:
            for table, ids in targets.items():
                c.execute(text(
                    f'UPDATE {table} SET deleted_at=:at, deleted_by=1 WHERE tenant_id=1 AND id=ANY(:ids)'),
                    {'at': now, 'ids': ids})
        with factory() as db:
            purge = PurgeService(db, Principal(1, 1, frozenset({'admin'})))
            # A project linked to this client blocks its parent purge even when
            # trashed, so purge the child first as the operator would.
            assert purge.purge('projects', [client_id])['deleted_ids'] == [client_id]
            assert purge.purge('clients', [client_id])['deleted_ids'] == [client_id]
            db.commit()
    else:
        raise AssertionError(how)
    if result is not None:
        assert result.status == 200, result.text


# Criterion 9: the feed needs a device token and the cursor is device-bound
def test_feed_requires_a_device_token_and_rejects_another_devices_cursor(api):
    call, factory, _ = api
    set_password(factory, 3)
    set_password(factory, 1)
    registered = body(register(call))
    access = registered['access_token']

    web = call('GET', '/api/sync/changes', user=3)
    assert web.status == 403, web.text
    assert_no_store(web)
    assert body(web)['code'] == 'device_token_required'

    status, page = feed(call, access)
    assert status == 200, page
    other = body(register(call, name='Second phone'))
    status, mismatch = feed(call, other['access_token'], cursor=page['cursor'])
    assert status == 400, mismatch
    assert mismatch['code'] == 'invalid_cursor'

    admin = body(register(call, email=ADMIN_EMAIL))
    status, foreign = feed(call, admin['access_token'], cursor=page['cursor'])
    assert status == 400 and foreign['code'] == 'invalid_cursor', foreign


# Criterion 10: removals reach only devices that held the record
def test_reassignment_removes_the_tree_for_holder_and_nothing_for_unrelated_user(api):
    call, factory, _ = api
    set_password(factory, 3)
    add_user(factory, 4, 'fourth@example.test')
    expected = seed_client_tree(factory, CLIENT_ID)
    seed_client_tree(factory, OTHER_CLIENT_ID, owner=4)
    unrelated_uuid = uuid_of(factory, 'clients', OTHER_CLIENT_ID)

    holder = body(register(call, name='Holder'))
    other = body(register(call, email='fourth@example.test', name='Other'))
    _, holder_cursor = drain(call, holder['access_token'])
    c_changes, c_cursor = drain(call, other['access_token'])
    assert ('clients', unrelated_uuid) in pairs(c_changes)

    mutate(factory, call, 'reassign')

    changes, _ = drain(call, holder['access_token'], cursor=holder_cursor)
    assert expected <= removals(changes), sorted(removals(changes))

    other_changes, _ = drain(call, other['access_token'], cursor=c_cursor)
    assert not (expected & removals(other_changes)), sorted(removals(other_changes))


@pytest.mark.parametrize('how', ['trash', 'archive', 'purge'])
def test_trash_archive_and_purge_remove_only_what_the_device_held(api, how):
    call, factory, _ = api
    set_password(factory, 3)
    add_user(factory, 4, 'fourth@example.test')
    tree = seed_client_tree(factory, CLIENT_ID)
    seed_client_tree(factory, OTHER_CLIENT_ID, owner=4)

    holder = body(register(call, name='Holder'))
    other = body(register(call, email='fourth@example.test', name='Other'))
    _, holder_cursor = drain(call, holder['access_token'])
    _, other_cursor = drain(call, other['access_token'])

    mutate(factory, call, how)

    if how == 'archive':
        expected = {('projects', uuid_of(factory, 'projects', CLIENT_ID)),
                    ('interactions', uuid_of(factory, 'interactions', project_call_id(CLIENT_ID)))}
    else:
        expected = tree

    changes, _ = drain(call, holder['access_token'], cursor=holder_cursor)
    assert expected <= removals(changes), sorted(removals(changes))
    assert all(change['op'] == 'remove' and set(change) == {'entity', 'op', 'uuid'}
               for change in changes if (change['entity'], change['uuid']) in expected)

    other_changes, _ = drain(call, other['access_token'], cursor=other_cursor)
    assert not (expected & removals(other_changes)), sorted(removals(other_changes))


# Criterion 11: lost responses repeat removals; acks, stale cursors and full syncs do not
def test_lost_page_re_emits_removals_until_the_retry_is_acknowledged(api):
    call, factory, _ = api
    set_password(factory, 3)
    expected = seed_client_tree(factory, CLIENT_ID)
    token = body(register(call))['access_token']
    _, full_cursor = drain(call, token)

    mutate(factory, call, 'reassign')
    status, lost = feed(call, token, cursor=full_cursor)
    assert status == 200, lost
    assert expected <= removals(lost['changes'])
    assert lost['cursor'] != full_cursor

    status, retry = feed(call, token, cursor=full_cursor)  # same cursor: response was lost
    assert status == 200, retry
    assert expected <= removals(retry['changes'])

    status, acked = feed(call, token, cursor=retry['cursor'])
    assert status == 200, acked
    assert not (expected & removals(acked['changes'])), sorted(removals(acked['changes']))


def test_acked_cursor_becomes_stale_and_forces_a_resync(api):
    call, factory, _ = api
    set_password(factory, 3)
    seed_client_tree(factory, CLIENT_ID)
    token = body(register(call))['access_token']
    _, full_cursor = drain(call, token)

    mutate(factory, call, 'reassign')
    status, page = feed(call, token, cursor=full_cursor)
    assert status == 200, page
    status, acked = feed(call, token, cursor=page['cursor'])
    assert status == 200, acked

    status, stale = feed(call, token, cursor=full_cursor)
    assert status == 410, stale
    assert stale['code'] == 'resync_required'


def test_full_sync_clears_holdings_and_stops_old_removals(api):
    call, factory, _ = api
    set_password(factory, 3)
    add_user(factory, 4, 'fourth@example.test')
    expected = seed_client_tree(factory, CLIENT_ID)
    token = body(register(call))['access_token']
    _, full_cursor = drain(call, token)
    device_id = body(call('GET', '/api/devices', token=token))[0]['id']
    assert expected <= holdings(factory, device_id)

    mutate(factory, call, 'reassign')
    status, pending = feed(call, token, cursor=full_cursor)
    assert status == 200 and expected <= removals(pending['changes'])

    status, fresh = feed(call, token)  # a new full sync starts a new generation
    assert status == 200, fresh
    assert not (expected & holdings(factory, device_id))
    assert pairs(fresh['changes']) & expected == set()

    # The record's access changes again while still hidden: without a holding
    # there is no removal to send.
    mutate(factory, call, 'reassign_to_four')
    changes, _ = drain(call, token, cursor=fresh['cursor'])
    assert not (expected & removals(changes)), sorted(removals(changes))


# Criterion 12: two devices of one user hold independent sets
def test_two_devices_of_one_user_get_removals_only_for_what_each_held(api):
    call, factory, _ = api
    set_password(factory, 3)
    first_tree = seed_client_tree(factory, CLIENT_ID)
    second_tree = seed_client_tree(factory, OTHER_CLIENT_ID)
    device_a = body(register(call, name='Phone A'))
    _, a_cursor = drain(call, device_a['access_token'])

    mutate(factory, call, 'reassign', client_id=CLIENT_ID)
    device_b = body(register(call, name='Phone B'))
    _, b_cursor = drain(call, device_b['access_token'])

    mutate(factory, call, 'reassign', client_id=OTHER_CLIENT_ID)

    a_changes, _ = drain(call, device_a['access_token'], cursor=a_cursor)
    b_changes, _ = drain(call, device_b['access_token'], cursor=b_cursor)
    assert first_tree <= removals(a_changes) and second_tree <= removals(a_changes)
    assert second_tree <= removals(b_changes)
    assert not (first_tree & removals(b_changes)), sorted(removals(b_changes))


# Criterion 13: revocation clears holdings; holdings stay in their tenant
@pytest.mark.parametrize('how', ['logout', 'owner_delete', 'admin_delete',
                                'refresh_reuse', 'deactivation'])
def test_revocation_clears_holdings_and_the_feed_refuses_the_device(api, how):
    call, factory, _ = api
    set_password(factory, 3)
    seed_client_tree(factory, CLIENT_ID)
    registered = body(register(call))
    token = registered['access_token']
    device_id = registered['device']['id']
    _, full_cursor = drain(call, token)
    assert holdings(factory, device_id)

    if how == 'logout':
        result = call('POST', '/api/auth/device/logout', token=token)
    elif how == 'owner_delete':
        result = call('DELETE', f'/api/devices/{device_id}', user=3)
    elif how == 'admin_delete':
        result = call('DELETE', f'/api/devices/{device_id}', user=1)
    elif how == 'refresh_reuse':
        rotated = body(refresh(call, registered['refresh_token']))
        with factory() as db:
            db.get(Device, UUID(device_id)).previous_valid_until = \
                datetime.utcnow() - timedelta(seconds=1)
            db.commit()
        result = refresh(call, registered['refresh_token'])
        assert result.status == 401 and body(result)['code'] == 'device_revoked'
        assert rotated['refresh_token'] != registered['refresh_token']
    else:
        result = call('PUT', '/api/users/3/toggle-active', user=1)

    if how != 'refresh_reuse':
        assert result.status in (200, 204), result.text
    assert holdings(factory, device_id) == set()
    assert device_by_id(factory, device_id).revoked_at is not None

    status, denied = feed(call, token, cursor=full_cursor)
    assert status == 401, denied
    if how != 'deactivation':
        assert denied['code'] == 'device_revoked', denied


def test_late_previous_secret_replay_revokes_the_device_and_clears_holdings(api):
    call, factory, _ = api
    set_password(factory, 3)
    tree = seed_client_tree(factory, CLIENT_ID)
    first = body(register(call))
    token = first['access_token']
    device_id = first['device']['id']
    drain(call, token)
    assert tree <= holdings(factory, device_id)

    second = body(refresh(call, first['refresh_token']))
    with factory() as db:
        db.get(Device, UUID(device_id)).previous_valid_until = \
            datetime.utcnow() - timedelta(seconds=1)
        db.commit()

    stolen = refresh(call, first['refresh_token'])

    assert stolen.status == 401 and body(stolen)['code'] == 'device_revoked'
    assert holdings(factory, device_id) == set()
    assert device_by_id(factory, device_id).revoked_at is not None
    assert second['refresh_token'] != first['refresh_token']


def test_holdings_never_cross_tenants(api):
    call, factory, _ = api
    set_password(factory, 3)
    with factory() as db:
        db.get(User, 2).password_hash = auth_utils.hash_password('OtherTenantPass!123')
        db.get(Tenant, 2).is_active = True
        db.commit()
    seed_client_tree(factory, CLIENT_ID)
    own = body(register(call, name='Tenant one'))
    foreign = body(register(call, email='b@example.test', password='OtherTenantPass!123',
                            name='Tenant two'))
    own_changes, _ = drain(call, own['access_token'])
    drain(call, foreign['access_token'])
    own_before = holdings(factory, own['device']['id'])
    foreign_before = holdings(factory, foreign['device']['id'])
    assert own_before and foreign_before
    assert not (own_before & foreign_before)

    status, fresh = feed(call, own['access_token'])
    assert status == 200, fresh
    assert pairs(own_changes) & foreign_before == set()
    assert holdings(factory, foreign['device']['id']) == foreign_before
    assert not (holdings(factory, own['device']['id']) & foreign_before)
    with factory() as db:
        assert {row.tenant_id for row in db.query(DeviceHolding).all()} == {1, 2}


# Criterion 14 and review round 1: visibility changes still upsert for a device
# that never held the records, including inherited children.
@pytest.mark.parametrize('how', ['reassign_back', 'restore'])
def test_newly_visible_inherited_children_are_upserted_for_a_device_that_never_held_them(api, how):
    call, factory, _ = api
    set_password(factory, 3)
    tree = seed_client_tree(factory, CLIENT_ID)
    hidden = body(register(call, name='Original holder'))
    drain(call, hidden['access_token'])

    if how == 'reassign_back':
        mutate(factory, call, 'reassign')
    else:
        mutate(factory, call, 'trash')

    newcomer = body(register(call, name='Never held the tree'))
    newcomer_id = newcomer['device']['id']
    newcomer_changes, newcomer_cursor = drain(call, newcomer['access_token'])
    assert not (tree & pairs(newcomer_changes))
    assert not (tree & holdings(factory, newcomer_id))

    if how == 'reassign_back':
        mutate(factory, call, 'reassign_to_three')
    else:
        mutate(factory, call, 'restore')

    changes, _ = drain(call, newcomer['access_token'], cursor=newcomer_cursor)
    visible = upserts(changes)
    assert tree <= visible, sorted(tree - visible)
    assert all(change['op'] == 'upsert' for change in changes
               if (change['entity'], change['uuid']) in tree)


def test_limit_one_skips_unheld_candidates_before_a_held_removal(api):
    call, factory, _ = api
    set_password(factory, 3)
    add_user(factory, 4, 'fourth@example.test')
    held_tree = seed_client_tree(factory, CLIENT_ID)
    unheld_tree = seed_client_tree(factory, UNHELD_CLIENT_ID, owner=4)
    token = body(register(call))['access_token']
    _, cursor = drain(call, token)

    mutate(factory, call, 'reassign', client_id=CLIENT_ID)
    mutate(factory, call, 'reassign', client_id=UNHELD_CLIENT_ID)

    status, page = feed(call, token, cursor=cursor, limit=1)
    assert status == 200, page
    assert len(page['changes']) == 1
    assert set(pairs(page['changes'])) <= held_tree
    assert not (unheld_tree & removals(page['changes']))

    rest, _ = drain(call, token, cursor=page['cursor'], limit=1)
    assert held_tree <= removals(page['changes']) | removals(rest)
    assert not (unheld_tree & (removals(page['changes']) | removals(rest)))


# Review round 1: a password reset after auth is caught under the feed's lock.
def test_password_reset_between_auth_and_feed_lock_is_caught(api, monkeypatch):
    import app.services.devices as device_service

    call, factory, _ = api
    set_password(factory, 3)
    seed_client_tree(factory, CLIENT_ID)
    token = body(register(call))['access_token']
    drain(call, token)
    original = device_service.current_account

    def reset_then_recheck(service, user_id):
        with factory() as db:
            db.get(User, user_id).password_hash = auth_utils.hash_password('ResetFeedPass!123')
            db.commit()
        return original(service, user_id)

    monkeypatch.setattr(device_service, 'current_account', reset_then_recheck)

    status, denied = feed(call, token)
    assert status == 401, denied
    assert denied['code'] == 'device_revoked'


# Review round 1 and 2: HEAD is refused without advancing sync state.
def test_head_feed_returns_405_with_allow_header_and_changes_no_device_state(api):
    call, factory, _ = api
    set_password(factory, 3)
    tree = seed_client_tree(factory, CLIENT_ID)
    token = body(register(call))['access_token']
    device_id = body(call('GET', '/api/devices', token=token))[0]['id']
    drain(call, token)

    before_state = device_state(factory, device_id)
    before_holdings = holdings(factory, device_id)
    assert tree <= before_holdings

    result = call('HEAD', '/api/sync/changes', token=token)

    assert result.status == 405, result.text
    assert result.headers['allow'] == 'GET, OPTIONS'
    assert_no_store(result)
    assert device_state(factory, device_id) == before_state
    assert holdings(factory, device_id) == before_holdings
