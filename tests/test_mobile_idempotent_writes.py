"""F-5: Idempotency-Key creates and updates are replay-safe.

The HTTP cases exercise IdempotencyService through the twelve in-scope routes:
POST and PUT /<id> for clients, leads, projects, accounts, contacts and
interactions. A successful outcome is stored in write_receipts in the same
transaction as the write; a retry replays an id-only acknowledgement with
Idempotent-Replayed, a different request with the same key returns 409
idempotency_conflict, and a failed write stores nothing. The retention case
covers scripts/prune_tombstones.py.
"""
import asyncio
import json
import os
import re
import sys
import threading
import time
from collections import namedtuple
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from authlib.jose import jwt
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.models import Account, Client, Contact, Interaction, Lead, Project, WriteReceipt
from app.services.ai_actions import payload_hash
from app.services.idempotency import IdempotencyConflict, IdempotencyKeyInvalid, IdempotencyService
from test_security_boundaries import crm as crm  # noqa: F401
from test_security_boundaries import rls_runtime as rls_runtime  # noqa: F401

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prune_tombstones  # noqa: E402

HEADER = 'Idempotency-Key'
REPLAY = 'Idempotent-Replayed'
Result = namedtuple('Result', 'status text headers')

CREATE_CASES = [
    ('POST', '/api/clients', {'name': 'Idem client'}, Client),
    ('POST', '/api/leads', {'name': 'Idem lead'}, Lead),
    ('POST', '/api/projects', {'project_name': 'Idem project', 'client_id': 1}, Project),
    ('POST', '/api/accounts', {'account_number': 'IDEM-1', 'client_id': 1}, Account),
    ('POST', '/api/contacts', {'first_name': 'Idem contact', 'client_id': 1}, Contact),
    ('POST', '/api/interactions',
     {'summary': 'Idem interaction', 'contact_date': '2026-09-06T12:00:00', 'client_id': 1},
     Interaction),
]
UPDATE_CASES = [
    ('PUT', '/api/clients/1', {'name': 'Renamed client'}, Client),
    ('PUT', '/api/leads/1', {'name': 'Renamed lead'}, Lead),
    ('PUT', '/api/projects/1', {'project_name': 'Renamed project'}, Project),
    ('PUT', '/api/accounts/1', {'account_number': 'IDEM-UPDATED'}, Account),
    ('PUT', '/api/contacts/1', {'first_name': 'Renamed contact'}, Contact),
    ('PUT', '/api/interactions/1', {'summary': 'Renamed interaction'}, Interaction),
]
ALL_CASES = CREATE_CASES + UPDATE_CASES
CASE_IDS = [f'{method}-{path}' for method, path, _, _ in ALL_CASES]


@pytest.fixture
def api(crm):
    """The shared crm fixture plus an Idempotency-Key-aware test client."""
    _, factory, app = crm

    def call(method, path, body=None, *, key=None, user=1):
        payload = {'sub': user, 'exp': int(time.time()) + 300, 'roles': ['admin']}
        token = jwt.encode({'alg': 'HS256'}, payload, app.config['SECRET_KEY']).decode()
        headers = {'Authorization': f'Bearer {token}'}
        if key is not None:
            headers[HEADER] = key

        async def run():
            response = await app.test_client().open(method=method, path=path, json=body,
                                                    headers=headers)
            return Result(response.status_code, await response.get_data(as_text=True),
                          dict(response.headers))

        return asyncio.run(run())

    return call, factory, app


def new_key():
    return 'f5' + uuid4().hex


def payload(result):
    return json.loads(result.text)


def header(result, name):
    return next((value for key, value in result.headers.items() if key.lower() == name.lower()),
                None)


def record_id(result, path):
    body = payload(result)
    if type(body.get('id')) is int:
        return body['id']
    target = re.search(r'/(\d+)$', path)
    assert target is not None, body
    return int(target.group(1))


def count(factory, model, **filters):
    with factory() as db:
        query = db.query(model)
        for field, value in filters.items():
            query = query.filter(getattr(model, field) == value)
        return query.count()


def receipts(factory, key=None):
    with factory() as db:
        query = db.query(WriteReceipt)
        if key is not None:
            query = query.filter_by(request_key=key)
        return [{'tenant_id': row.tenant_id, 'user_id': row.user_id,
                 'request_key': row.request_key, 'endpoint': row.endpoint,
                 'input_hash': row.input_hash, 'status_code': row.status_code,
                 'response': row.response}
                for row in query.order_by(WriteReceipt.id).all()]


def is_postgresql(factory):
    return factory.kw['bind'].dialect.name == 'postgresql'


def fingerprint_for(app, body):
    endpoint = next(rule.endpoint for rule in app.url_map.iter_rules()
                    if rule.rule == '/api/clients' and 'POST' in rule.methods)
    return endpoint, payload_hash({'endpoint': endpoint, 'target': {}, 'input': body})


def wait_for_advisory_waiter(engine, holder_pid, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with engine.connect() as monitor:
            waiting = monitor.execute(text(
                'SELECT count(*) FROM pg_stat_activity WHERE pid<>:pid '
                "AND wait_event_type='Lock' AND wait_event='advisory' "
                "AND query LIKE '%pg_advisory_xact_lock%'"), {'pid': holder_pid}).scalar_one()
        if waiting:
            return
        time.sleep(0.02)
    raise AssertionError('the retry did not wait for the advisory lock')


def seed_holder_write(factory, app, key, body):
    """Hold the key's advisory lock with an uncommitted client write and receipt."""
    endpoint, fingerprint = fingerprint_for(app, body)
    holder = factory()
    holder_pid = holder.connection().execute(text('SELECT pg_backend_pid()')).scalar_one()
    holder.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))'),
                   {'key': f'write-receipt:1:1:{key}'})
    client = Client(tenant_id=1, created_by=1, name=body['name'])
    holder.add(client)
    holder.flush()
    holder.add(WriteReceipt(tenant_id=1, user_id=1, request_key=key,
                            endpoint=endpoint[:100], input_hash=fingerprint,
                            status_code=201, response={'id': client.id}))
    holder.flush()
    return holder, holder_pid, client.id


def test_idempotency_service_exposes_the_brief_contract(crm):
    """IdempotencyService.begin validates the header before any write starts."""
    from quart import request
    from quart import Quart
    from app.services.principal import Principal

    assert issubclass(IdempotencyKeyInvalid, ValueError)
    assert IdempotencyConflict.code == 'idempotency_conflict'
    assert callable(IdempotencyService.begin) and callable(IdempotencyService.finish)

    _, factory, _ = crm
    app = Quart(__name__)

    async def run():
        with factory() as db:
            async with app.test_request_context('/api/clients', method='POST',
                                                headers={HEADER: 'short'}):
                request.principal = Principal(1, 1, frozenset({'admin'}))
                with pytest.raises(IdempotencyKeyInvalid):
                    IdempotencyService(db, request.principal).begin({'name': 'x'})
    asyncio.run(run())


@pytest.mark.parametrize('method,path,body,model', ALL_CASES, ids=CASE_IDS)
def test_replay_acknowledges_one_write_with_only_the_id(api, method, path, body, model):
    call, factory, _ = api
    key = new_key()
    before = count(factory, model)

    first = call(method, path, body, key=key)
    second = call(method, path, body, key=key)

    assert first.status == (201 if method == 'POST' else 200), first.text
    assert second.status == first.status
    assert payload(second) == {'id': record_id(first, path)}
    assert header(second, REPLAY) == 'true'
    assert header(second, 'Cache-Control') == 'no-store'
    assert header(first, REPLAY) is None
    assert count(factory, model) == before + (1 if method == 'POST' else 0)

    saved = receipts(factory, key)
    assert len(saved) == 1
    assert saved[0]['status_code'] == first.status
    assert saved[0]['response'] == {'id': record_id(first, path)}


@pytest.mark.parametrize('variant', ['body', 'target', 'endpoint'])
def test_conflicting_key_is_409_and_writes_nothing(api, variant):
    call, factory, _ = api
    key = new_key()
    other = call('POST', '/api/clients', {'name': 'Other client'})
    assert other.status == 201, other.text
    other_id = payload(other)['id']
    initial = count(factory, Client), count(factory, Lead)

    first = call('PUT', '/api/clients/1', {'name': 'First'}, key=key)
    assert first.status == 200, first.text

    if variant == 'body':
        path, body = '/api/clients/1', {'name': 'Second'}
    elif variant == 'target':
        path, body = f'/api/clients/{other_id}', {'name': 'First'}
    else:
        path, body = '/api/leads/1', {'name': 'First'}
    conflict = call('PUT', path, body, key=key)

    assert conflict.status == 409, conflict.text
    assert payload(conflict)['code'] == 'idempotency_conflict'
    assert header(conflict, REPLAY) is None
    assert (count(factory, Client), count(factory, Lead)) == initial
    assert len(receipts(factory, key)) == 1
    with factory() as db:
        assert db.get(Client, 1).name == 'First'
        assert db.get(Client, other_id).name == 'Other client'


def test_key_scope_is_tenant_and_user(api):
    call, factory, _ = api
    key, body = new_key(), {'name': 'Scoped client'}
    before_one, before_two = (count(factory, Client, tenant_id=1),
                              count(factory, Client, tenant_id=2))

    first = call('POST', '/api/clients', body, key=key, user=1)
    same_tenant = call('POST', '/api/clients', body, key=key, user=3)
    other_tenant = call('POST', '/api/clients', body, key=key, user=2)

    assert [r.status for r in (first, same_tenant, other_tenant)] == [201, 201, 201]
    assert all(header(r, REPLAY) is None for r in (first, same_tenant, other_tenant))
    assert len({payload(r)['id'] for r in (first, same_tenant, other_tenant)}) == 3
    assert count(factory, Client, tenant_id=1) == before_one + 2
    assert count(factory, Client, tenant_id=2) == before_two + 1
    assert {(r['tenant_id'], r['user_id']) for r in receipts(factory, key)} == \
        {(1, 1), (1, 3), (2, 2)}


def test_no_header_keeps_create_behaviour_unchanged(api):
    call, factory, _ = api
    body = {'name': 'No key'}
    before = count(factory, Client)

    first = call('POST', '/api/clients', body)
    second = call('POST', '/api/clients', body)

    assert [r.status for r in (first, second)] == [201, 201]
    assert all(header(r, REPLAY) is None for r in (first, second))
    assert payload(first)['id'] != payload(second)['id']
    assert count(factory, Client) == before + 2
    assert receipts(factory) == []


@pytest.mark.parametrize('bad_key', ['short', 'x' * 65, 'a' * 8 + '!' + 'b' * 7])
def test_invalid_key_returns_400_and_writes_nothing(api, bad_key):
    call, factory, _ = api
    before = count(factory, Client)

    result = call('POST', '/api/clients', {'name': 'Invalid key'}, key=bad_key)

    assert result.status == 400, result.text
    assert count(factory, Client) == before
    assert receipts(factory) == []


@pytest.mark.parametrize('path,body,corrected,model', [
    ('/api/accounts', {'client_id': 99999, 'account_number': 'ATOMIC'},
     {'client_id': 1, 'account_number': 'ATOMIC'}, Account),
    ('/api/contacts', {'first_name': 'Atomic', 'client_id': 1, 'lead_id': 1},
     {'first_name': 'Atomic', 'client_id': 1}, Contact),
])
def test_failed_write_stores_no_receipt_and_same_key_succeeds_after_fix(
        api, path, body, corrected, model):
    call, factory, _ = api
    key = new_key()
    before = count(factory, model)

    failed = call('POST', path, body, key=key)
    assert failed.status in (400, 404), failed.text
    assert count(factory, model) == before
    assert receipts(factory, key) == []

    fixed = call('POST', path, corrected, key=key)
    assert fixed.status == 201, fixed.text
    assert count(factory, model) == before + 1
    saved = receipts(factory, key)
    assert len(saved) == 1 and saved[0]['status_code'] == 201

    replay = call('POST', path, corrected, key=key)
    assert replay.status == 201
    assert payload(replay) == {'id': payload(fixed)['id']}
    assert header(replay, REPLAY) == 'true'


def test_concurrent_retries_on_postgresql_produce_one_write_and_one_replay(api):
    call, factory, _ = api
    if not is_postgresql(factory):
        pytest.skip('Requires PostgreSQL advisory locks')
    key, body = new_key(), {'name': 'Concurrent client'}
    before = count(factory, Client)
    barrier = threading.Barrier(2)
    results = {}

    def worker(index):
        try:
            barrier.wait(timeout=10)
            results[index] = call('POST', '/api/clients', body, key=key)
        except BaseException as error:  # reported by the assertions below
            results[index] = error

    threads = [threading.Thread(target=worker, args=(index,), daemon=True)
               for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert all(not thread.is_alive() for thread in threads), 'a concurrent request hung'
    assert all(isinstance(result, Result) for result in results.values()), results

    responses = list(results.values())
    assert sorted(result.status for result in responses) == [201, 201]
    assert all(payload(result) == payload(responses[0]) for result in responses)
    replay_flags = [header(result, REPLAY) for result in responses]
    assert replay_flags.count('true') == 1
    assert replay_flags.count(None) == 1
    assert count(factory, Client) == before + 1
    assert len(receipts(factory, key)) == 1


@pytest.mark.parametrize('winner_commits', [True, False])
def test_waiting_retry_follows_the_winners_commit_or_rollback(api, winner_commits):
    call, factory, app = api
    if not is_postgresql(factory):
        pytest.skip('Requires PostgreSQL advisory locks')
    key, body = new_key(), {'name': 'Held client'}
    before = count(factory, Client)
    holder, holder_pid, holder_id = seed_holder_write(factory, app, key, body)
    results = {}
    thread = threading.Thread(
        target=lambda: results.setdefault('waiter', call('POST', '/api/clients', body, key=key)),
        daemon=True)
    try:
        thread.start()
        wait_for_advisory_waiter(factory.kw['bind'], holder_pid)
        if winner_commits:
            holder.commit()
        else:
            holder.rollback()
        thread.join(timeout=30)
    finally:
        if holder.is_active:
            holder.rollback()
        if thread.is_alive():
            thread.join(timeout=5)
    assert not thread.is_alive(), 'the waiting request did not finish'
    waiter = results['waiter']
    assert isinstance(waiter, Result), waiter
    assert waiter.status == 201, waiter.text
    assert count(factory, Client) == before + 1
    assert len(receipts(factory, key)) == 1

    if winner_commits:
        assert header(waiter, REPLAY) == 'true'
        assert payload(waiter) == {'id': holder_id}
    else:
        assert header(waiter, REPLAY) is None
        assert payload(waiter)['id'] != holder_id


def test_waiting_retry_past_the_lock_timeout_returns_409_and_retry_after(api):
    call, factory, _ = api
    if not is_postgresql(factory):
        pytest.skip('Requires PostgreSQL advisory locks')
    key, body = new_key(), {'name': 'Timed out client'}
    before = count(factory, Client)
    holder = factory()
    holder_pid = holder.connection().execute(text('SELECT pg_backend_pid()')).scalar_one()
    holder.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))'),
                   {'key': f'write-receipt:1:1:{key}'})
    results = {}
    thread = threading.Thread(
        target=lambda: results.setdefault('waiter', call('POST', '/api/clients', body, key=key)),
        daemon=True)
    try:
        thread.start()
        wait_for_advisory_waiter(factory.kw['bind'], holder_pid)
        thread.join(timeout=10)  # 5s lock timeout, plus slack
    finally:
        if holder.is_active:
            holder.rollback()
        if thread.is_alive():
            thread.join(timeout=5)
    assert not thread.is_alive(), 'the lock timeout did not end the waiting request'
    waiter = results['waiter']
    assert isinstance(waiter, Result), waiter
    assert waiter.status == 409, waiter.text
    assert payload(waiter)['code'] == 'idempotency_in_progress'
    assert header(waiter, 'Retry-After') == '1'
    assert count(factory, Client) == before
    assert receipts(factory, key) == []


def test_receipt_failure_after_business_flush_rolls_back_both(api, monkeypatch):
    from app.services import idempotency

    call, factory, _ = api
    original = idempotency.IdempotencyService.finish

    def failing_finish(self, result, status):
        original(self, result, status)  # the receipt is flushed here
        raise RuntimeError('receipt insert failed')

    monkeypatch.setattr(idempotency.IdempotencyService, 'finish', failing_finish)
    key = new_key()
    before = count(factory, Client)

    result = call('POST', '/api/clients', {'name': 'Rollback both'}, key=key)

    assert result.status == 500, result.text
    assert count(factory, Client) == before
    assert receipts(factory, key) == []


def test_replay_after_parent_reassignment_returns_only_an_id(api):
    call, factory, _ = api
    key, body = new_key(), {'project_name': 'Parent project', 'client_id': 1}
    created = call('POST', '/api/projects', body, key=key)
    assert created.status == 201, created.text
    project_id = payload(created)['id']

    with factory() as db:
        project = db.get(Project, project_id)
        project.client_id = None
        project.lead_id = 1
        db.commit()

    replay = call('POST', '/api/projects', body, key=key)

    assert replay.status == 201
    assert header(replay, REPLAY) == 'true'
    assert payload(replay) == {'id': project_id}
    assert 'project_name' not in replay.text
    assert 'client_name' not in replay.text


def test_put_replay_does_not_write_again(api):
    call, factory, _ = api
    key, path, body = new_key(), '/api/clients/1', {'name': 'Write once'}
    marker = 'SELECT updated_at, sync_change_xid FROM clients WHERE id=1'

    with factory() as db:
        before = db.execute(text(marker)).one()
    first = call('PUT', path, body, key=key)
    assert first.status == 200, first.text
    with factory() as db:
        after_first = db.execute(text(marker)).one()
    assert after_first.sync_change_xid != before.sync_change_xid

    time.sleep(0.01)
    replay = call('PUT', path, body, key=key)

    assert replay.status == 200
    assert header(replay, REPLAY) == 'true'
    assert payload(replay) == {'id': 1}
    with factory() as db:
        assert db.execute(text(marker)).one() == after_first


def test_runtime_role_isolation_privileges_and_contract(rls_runtime):
    from app.services.database_context import bind_principal
    from app.services.principal import Principal
    from ops.isolation_contract import TABLES, verify_isolation_contract

    assert 'write_receipts' in TABLES
    factory, admin, schema = rls_runtime
    keys = {'own': new_key(), 'other_user': new_key(), 'other_tenant': new_key()}
    now = datetime.utcnow()
    with admin() as db:
        db.add_all([
            WriteReceipt(tenant_id=1, user_id=1, request_key=keys['own'],
                         endpoint='create_client', input_hash='a' * 64,
                         status_code=201, response={'id': 10}, created_at=now),
            WriteReceipt(tenant_id=1, user_id=3, request_key=keys['other_user'],
                         endpoint='create_client', input_hash='b' * 64,
                         status_code=201, response={'id': 11}, created_at=now),
            WriteReceipt(tenant_id=2, user_id=2, request_key=keys['other_tenant'],
                         endpoint='create_client', input_hash='c' * 64,
                         status_code=201, response={'id': 12}, created_at=now),
        ])
        db.commit()

    with factory() as db:
        bind_principal(db, Principal(1, 1, frozenset({'admin'})))
        assert db.execute(text('SELECT request_key FROM write_receipts')).scalars().all() == \
            [keys['own']]
        for statement in ('UPDATE write_receipts SET status_code=200 WHERE request_key=:key',
                          'DELETE FROM write_receipts WHERE request_key=:key'):
            with db.begin_nested() as savepoint:
                with pytest.raises(DBAPIError) as denied:
                    db.execute(text(statement), {'key': keys['own']})
                assert denied.value.orig.pgcode == '42501'
                savepoint.rollback()
        db.rollback()

    with factory() as db:
        bind_principal(db, Principal(2, 2, frozenset({'admin'})))
        assert db.execute(text('SELECT request_key FROM write_receipts')).scalars().all() == \
            [keys['other_tenant']]

    with admin() as db:
        db.execute(text(f'SET LOCAL search_path="{schema}"'))
        checked = verify_isolation_contract(db.connection(), schema,
                                            os.environ['SECURITY_TEST_ROLE'])
    assert checked['role_restricted'] and checked['tables'] == len(TABLES)


def test_prune_removes_only_receipts_older_than_90_days(api):
    call, factory, _ = api
    if not is_postgresql(factory):
        pytest.skip('Requires PostgreSQL')
    now = datetime.utcnow()
    cutoff = now - timedelta(days=90)
    young, old = new_key(), new_key()
    with factory() as db:
        db.add_all([
            WriteReceipt(tenant_id=1, user_id=1, request_key=young,
                         endpoint='create_client', input_hash='a' * 64, status_code=201,
                         response={'id': 1}, created_at=cutoff + timedelta(minutes=1)),
            WriteReceipt(tenant_id=1, user_id=1, request_key=old,
                         endpoint='create_client', input_hash='b' * 64, status_code=201,
                         response={'id': 2}, created_at=cutoff - timedelta(minutes=1)),
        ])
        db.commit()

        dry = prune_tombstones.prune(db.connection(), now, apply=False)
        assert dry == {'interactions': 0, 'contacts': 0, 'accounts': 0,
                       'sync_purges': 0, 'write_receipts': 1}
        db.commit()
        assert {row.request_key for row in db.query(WriteReceipt).all()} == {young, old}

        applied = prune_tombstones.prune(db.connection(), now, apply=True)
        assert applied == dry
        db.commit()
        assert [row.request_key for row in db.query(WriteReceipt).all()] == [young]
