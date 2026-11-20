"""F-7: Phase 0 signoff acceptance flows against the existing backend tiers.

These tests deliberately stay in the backend's `tests/` tree and use the shared
`crm` fixture, so they inherit the restricted runtime role when
`SECURITY_TEST_ROLE` is set. They prove the Phase 0 acceptance paragraph as one
scripted device flow, plus the two edge cases called out in Phase 0 item 7 and
the lead's offline-grace follow-up.
"""
import pytest
from sqlalchemy import text

from test_mobile_device_auth import (PASSWORD, api as api, body, refresh,
                                      register, set_password)
from test_mobile_device_auth_feed import (add_user, drain, feed, pairs, removals,
                                           upserts, uuid_of)
from test_security_boundaries import crm as crm  # noqa: F401


@pytest.fixture(autouse=True)
def fresh_rate_limits():
    """A register/refresh bucket is per case, as in the device-auth tier."""
    import app.utils.rate_limiter as limiter

    limiter._rate_limit_store.clear()
    yield
    limiter._rate_limit_store.clear()


def count(factory, table, criterion=''):
    with factory() as db:
        return db.execute(text(f'SELECT count(*) FROM {table} {criterion}')).scalar_one()


def test_phase0_end_to_end_scripted_client_pulls_creates_once_and_loses_access(api):
    """Password register -> full pull -> keyed create twice -> upsert -> removal."""
    call, factory, _ = api

    # Tenant 2 is the synthetic tenant. Its admin (user 2) stays a web caller;
    # the scripted client is a new ordinary user with a real password.
    with factory() as db:
        db.execute(text('UPDATE tenants SET is_active=true WHERE id=2'))
        db.commit()
    add_user(factory, 4, 'mobile-b@example.test', tenant_id=2)
    with factory.begin() as c:
        c.execute(text(
            'INSERT INTO clients (id, tenant_id, created_by, assigned_to, name) '
            "VALUES (500, 2, 2, 4, 'Phase 0 synthetic parent')"))
    parent = ('clients', uuid_of(factory, 'clients', 500))

    registered = body(register(call, email='mobile-b@example.test', password=PASSWORD,
                               name='Phase 0 iPhone'))
    assert registered['user']['id'] == 4 and registered['tenant']['id'] == 2
    token = registered['access_token']

    full_changes, cursor = drain(call, token, limit=200)
    assert parent in pairs(full_changes)

    key = 'phase0-synthetic-account-1'
    payload = {'account_number': 'PHASE0-1', 'account_name': 'Phase 0 account',
               'client_id': 500}
    created = call('POST', '/api/accounts', payload, token=token,
                   headers={'Idempotency-Key': key})
    assert created.status == 201, created.text
    record_id = body(created)['id']
    assert created.headers.get('idempotent-replayed') is None

    replay = call('POST', '/api/accounts', payload, token=token,
                  headers={'Idempotency-Key': key})
    assert replay.status == 201, replay.text
    assert replay.headers['idempotent-replayed'] == 'true'
    assert body(replay) == {'id': record_id}
    assert count(factory, 'accounts', "WHERE tenant_id=2 AND account_number='PHASE0-1'") == 1

    account = ('accounts', uuid_of(factory, 'accounts', record_id))
    changes, cursor = drain(call, token, cursor=cursor, limit=200)
    assert account in upserts(changes)

    reassigned = call('PUT', '/api/clients/500/assign', {'assigned_to': 2}, user=2)
    assert reassigned.status == 200, reassigned.text

    changes, _ = drain(call, token, cursor=cursor, limit=200)
    assert account in removals(changes)
    assert account not in upserts(changes)


def test_access_lost_between_pages_of_one_incremental_round_is_delivered_by_the_next_round(api):
    """A lower-id held record revoked mid-round is emitted by the following round."""
    call, factory, _ = api
    set_password(factory, 3)
    with factory.begin() as c:
        c.execute(text(
            'INSERT INTO clients (id, tenant_id, created_by, assigned_to, name) '
            "VALUES (800, 1, 1, 3, 'Mid-round target')"))
    target = ('clients', uuid_of(factory, 'clients', 800))
    token = body(register(call))['access_token']
    full_changes, cursor = drain(call, token, limit=200)
    assert target in pairs(full_changes)

    # Two higher-id changes have markers newer than the completed full round, so
    # the first incremental page consumes id 900 while the target id 800 is later
    # in the key order. The target's access change stays open until that page has
    # been served, making it a mid-round revocation below the page's lower id.
    with factory.begin() as c:
        c.execute(text(
            'INSERT INTO clients (id, tenant_id, created_by, assigned_to, name) '
            "VALUES (900, 1, 3, 3, 'Incremental page one')"))
        c.execute(text(
            'INSERT INTO clients (id, tenant_id, created_by, assigned_to, name) '
            "VALUES (901, 1, 3, 3, 'Incremental page two')"))

    with factory() as writer:
        writer.execute(text('UPDATE clients SET assigned_to=1 WHERE id=800'))
        status, first = feed(call, token, cursor=cursor, limit=1)
        assert status == 200, first
        assert first['has_more'] is True
        assert pairs(first['changes']) == {('clients', uuid_of(factory, 'clients', 900))}
        assert target not in removals(first['changes'])
        writer.commit()

    round_changes, next_round_cursor = drain(call, token, cursor=first['cursor'], limit=1)
    assert target not in removals(first['changes'] + round_changes)

    following_changes, _ = drain(call, token, cursor=next_round_cursor, limit=200)
    assert target in removals(following_changes)


def test_register_and_refresh_report_default_offline_grace(api, monkeypatch):
    monkeypatch.delenv('DEVICE_OFFLINE_GRACE_DAYS', raising=False)
    monkeypatch.delenv('DEVICE_REFRESH_DAYS', raising=False)
    call, factory, _ = api
    set_password(factory, 3)

    registered = body(register(call))
    assert registered['offline_grace_seconds'] == 604800
    refreshed = body(refresh(call, registered['refresh_token']))
    assert refreshed['offline_grace_seconds'] == 604800


def test_register_and_refresh_honour_offline_grace_days_override(api, monkeypatch):
    monkeypatch.setenv('DEVICE_OFFLINE_GRACE_DAYS', '3')
    monkeypatch.delenv('DEVICE_REFRESH_DAYS', raising=False)
    call, factory, _ = api
    set_password(factory, 3)

    registered = body(register(call))
    assert registered['offline_grace_seconds'] == 3 * 86400
    refreshed = body(refresh(call, registered['refresh_token']))
    assert refreshed['offline_grace_seconds'] == 3 * 86400
