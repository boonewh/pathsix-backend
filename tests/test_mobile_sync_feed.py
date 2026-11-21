"""Change feed for offline devices: cursor, visibility, markers and operations.

Most checks need PostgreSQL: the snapshot-based cursor and the marker triggers are
PostgreSQL features, and the route returns 501 on SQLite. They use the shared
`crm` fixture, so a missing SECURITY_TEST_DATABASE_URL skips them rather than
faking the database. The pure HTTP guards (bad limit, no auth) still run on the
default SQLite fixture.
"""
import asyncio
import base64
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from uuid import uuid4

import pytest
from authlib.jose import jwt
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.models import Account, Client, Contact, Device, Interaction, Lead, Project, User
from app.services.devices import credential_marker
from app.services.principal import Principal
from app.services.sync_feed import (
    CURSOR_MAX_AGE_DAYS,
    FIELDS,
    MAX_LIMIT,
    ResyncRequired,
    SyncFeedService,
    encode_cursor,
)
from purge_fixture import crm as base_crm
from test_project_archive import archive as archive
from test_security_boundaries import rls_runtime as rls_runtime
from test_verify_production_connector import remote_source

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prune_tombstones  # noqa: E402

ENTITIES = ('clients', 'leads', 'projects', 'accounts', 'contacts', 'interactions')
SECRET = 'test-only-signing-key'
ADMIN = Principal(1, 1, frozenset({'admin'}))
ORDINARY = Principal(3, 1, frozenset())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _pg(factory):
    if factory.kw['bind'].dialect.name != 'postgresql':
        pytest.skip('Requires PostgreSQL')


def _request(call, cursor=None, limit=None, user=1):
    params = {}
    if cursor is not None:
        params['cursor'] = cursor
    if limit is not None:
        params['limit'] = str(limit)
    path = '/api/sync/changes' + ('?' + urlencode(params) if params else '')
    status, body = call('GET', path, user=user)
    try:
        return status, json.loads(body)
    except json.JSONDecodeError:
        return status, body


def _page(call, cursor=None, limit=None, user=1):
    status, payload = _request(call, cursor, limit, user)
    assert status == 200, payload
    assert set(payload) == {'mode', 'changes', 'cursor', 'has_more'}
    assert isinstance(payload['has_more'], bool)
    assert payload['cursor']
    return payload


def _drain(call, cursor=None, limit=None, user=1, max_pages=100):
    """Every page must be full except the last; returns changes and final cursor."""
    changes, modes, pages = [], [], 0
    while pages < max_pages:
        payload = _page(call, cursor, limit, user)
        pages += 1
        modes.append(payload['mode'])
        size = len(payload['changes'])
        assert size <= (limit or MAX_LIMIT)
        if payload['has_more']:
            assert size == (limit or MAX_LIMIT), 'a non-final page was short'
        changes.extend(payload['changes'])
        cursor = payload['cursor']
        if not payload['has_more']:
            return changes, cursor, modes
    pytest.fail('the feed never finished paging')


def _pairs(changes):
    return [(change['entity'], change['uuid']) for change in changes]


def _assert_exactly_once(changes, expected):
    counts = Counter(_pairs(changes))
    assert set(counts) == set(expected), (sorted(counts), sorted(expected))
    assert all(value == 1 for value in counts.values()), counts


def _uuid(factory, table, record_id):
    with factory() as db:
        return str(db.execute(text(f'SELECT uuid FROM {table} WHERE id=:id'),
                              {'id': record_id}).scalar_one())


def _pair(factory, table, record_id):
    return table, _uuid(factory, table, record_id)


def _insert(connection, table, **values):
    if table == 'interactions':
        values.setdefault('followup_status', 'pending')
    columns = ', '.join(values)
    placeholders = ', '.join(':' + name for name in values)
    connection.execute(text(f'INSERT INTO {table} ({columns}) VALUES ({placeholders})'), values)


def _cursor_payload(cursor):
    body = cursor.split('.')[0]
    return json.loads(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4)))


def _count(factory, table, record_id):
    with factory() as db:
        return db.execute(text(f'SELECT count(*) FROM {table} WHERE id=:id'),
                          {'id': record_id}).scalar_one()


def _service_page(factory, principal, secret, cursor=None, limit=None, now=None):
    with factory() as db:
        return SyncFeedService(db, principal).page(cursor, limit, secret=secret, now=now)


def _response(app, factory, path, user=1):
    """A real HTTP response with a device token, so response headers can be checked."""
    with factory() as db:
        account = db.get(User, user)
        device = Device(id=uuid4(), tenant_id=account.tenant_id, user_id=account.id,
                        name='Feed response probe', platform='ios',
                        created_at=datetime.utcnow(), last_seen_at=datetime.utcnow(),
                        refresh_hash='a' * 64,
                        refresh_expires_at=datetime.utcnow() + timedelta(days=30),
                        credential_marker=credential_marker(account))
        db.add(device)
        db.commit()
        device_id = device.id
    payload = {'sub': user, 'exp': int(time.time()) + 300, 'roles': ['admin'],
               'did': str(device_id), 'typ': 'device'}
    token = jwt.encode({'alg': 'HS256'}, payload, app.config['SECRET_KEY']).decode()

    async def run():
        return await app.test_client().open(path, headers={'Authorization': f'Bearer {token}'})

    return asyncio.run(run())


@pytest.fixture
def crm(base_crm):
    """F-4's schema and helper, with device tokens for the feed endpoint.

    Only /api/sync/changes is switched: record mutations in these tests still
    use the ordinary web-token caller. One device per user is enough because a
    cursor is bound to its device; direct SyncFeedService cases call `page`
    without a device_id and keep the legacy web behaviour.
    """
    base_call, factory, app = base_crm
    devices = {}

    def device_for(user_id):
        if user_id not in devices:
            with factory() as db:
                account = db.get(User, user_id)
                device = Device(id=uuid4(), tenant_id=account.tenant_id, user_id=account.id,
                                name=f'Feed device {user_id}', platform='ios',
                                created_at=datetime.utcnow(), last_seen_at=datetime.utcnow(),
                                refresh_hash='a' * 64,
                                refresh_expires_at=datetime.utcnow() + timedelta(days=30),
                                credential_marker=credential_marker(account))
                db.add(device)
                db.commit()
                devices[user_id] = device.id
        return devices[user_id]

    def call(method, path, body=None, user=1, claims=None):
        if path.startswith('/api/sync/changes') and user:
            claims = dict(claims or {})
            claims.setdefault('did', str(device_for(user)))
            claims.setdefault('typ', 'device')
        return base_call(method, path, body, user=user, claims=claims)

    return call, factory, app


@pytest.fixture
def sqlite_crm(monkeypatch, request):
    """The shared crm fixture without PostgreSQL settings, for route-only guards."""
    monkeypatch.delenv('SECURITY_TEST_DATABASE_URL', raising=False)
    monkeypatch.delenv('SECURITY_TEST_ROLE', raising=False)
    return request.getfixturevalue('crm')


# ---------------------------------------------------------------------------
# 1. Full sync: paging, visibility, exclusions
# ---------------------------------------------------------------------------
def test_full_sync_pages_every_visible_record_once_for_admin_and_ordinary(crm):
    call, factory, _ = crm
    _pg(factory)
    deleted = datetime.utcnow()

    with factory.begin() as c:
        # Trashed parents, tombstones, an archived project and a tenant-2 row
        # must all be absent from a tenant-1 feed.
        _insert(c, 'clients', id=10, tenant_id=1, created_by=1, name='Trashed client',
                deleted_at=deleted, deleted_by=1)
        _insert(c, 'leads', id=10, tenant_id=1, created_by=1, name='Trashed lead',
                deleted_at=deleted, deleted_by=1)
        _insert(c, 'projects', id=10, tenant_id=1, created_by=1, project_name='Trashed project',
                project_status='pending', deleted_at=deleted, deleted_by=1)
        _insert(c, 'accounts', id=20, tenant_id=1, client_id=1, account_number='HIDDEN-A',
                deleted_at=deleted, deleted_by=1)
        _insert(c, 'contacts', id=20, tenant_id=1, client_id=1, first_name='Hidden',
                deleted_at=deleted, deleted_by=1)
        _insert(c, 'interactions', id=20, tenant_id=1, client_id=1, summary='Hidden',
                deleted_at=deleted, deleted_by=1)
        _insert(c, 'projects', id=30, tenant_id=1, created_by=1, project_name='Archived',
                project_status='pending', archived_at=deleted, archived_by=1)
        _insert(c, 'interactions', id=30, tenant_id=1, project_id=30, summary='Archived project')
        # Another user's same-tenant records: admin sees them, user 3 does not.
        _insert(c, 'clients', id=40, tenant_id=1, created_by=2, name='User 2 client')
        _insert(c, 'accounts', id=40, tenant_id=1, client_id=40, account_number='HIDDEN-B')
        _insert(c, 'contacts', id=40, tenant_id=1, client_id=40, first_name='User 2 contact')
        _insert(c, 'interactions', id=40, tenant_id=1, client_id=40, summary='User 2 interaction')
        # Records service policy does let user 3 see.
        _insert(c, 'clients', id=50, tenant_id=1, created_by=3, name='User 3 client')
        _insert(c, 'leads', id=50, tenant_id=1, created_by=3, name='User 3 lead')
        _insert(c, 'projects', id=50, tenant_id=1, created_by=3, project_name='User 3 project',
                project_status='pending')
        _insert(c, 'accounts', id=50, tenant_id=1, client_id=50, account_number='HIDDEN-C')
        _insert(c, 'contacts', id=50, tenant_id=1, client_id=50, first_name='User 3 contact')
        _insert(c, 'interactions', id=50, tenant_id=1, client_id=50, summary='User 3 interaction')

    admin_expected = {
        _pair(factory, 'clients', 1), _pair(factory, 'leads', 1),
        _pair(factory, 'projects', 1), _pair(factory, 'accounts', 1),
        _pair(factory, 'contacts', 1), _pair(factory, 'interactions', 1),
        _pair(factory, 'clients', 40), _pair(factory, 'accounts', 40),
        _pair(factory, 'contacts', 40), _pair(factory, 'interactions', 40),
        # An admin sees every live record in the tenant, including user 3's.
        _pair(factory, 'clients', 50), _pair(factory, 'leads', 50),
        _pair(factory, 'projects', 50), _pair(factory, 'accounts', 50),
        _pair(factory, 'contacts', 50), _pair(factory, 'interactions', 50),
    }
    ordinary_expected = {
        _pair(factory, 'clients', 50), _pair(factory, 'leads', 50),
        _pair(factory, 'projects', 50), _pair(factory, 'accounts', 50),
        _pair(factory, 'contacts', 50), _pair(factory, 'interactions', 50),
    }

    for limit in (1, 2, 200):
        changes, cursor, modes = _drain(call, limit=limit, user=1)
        assert set(modes) == {'full'}
        _assert_exactly_once(changes, admin_expected)
        assert all(change['op'] == 'upsert' for change in changes)
        # The final cursor starts the incremental round with no new writes.
        assert _page(call, cursor, limit=limit, user=1)['changes'] == []

    changes, _, modes = _drain(call, limit=2, user=3)
    assert set(modes) == {'full'}
    _assert_exactly_once(changes, ordinary_expected)
    assert all(change['op'] == 'upsert' for change in changes)


def test_records_carry_parent_uuids_and_iso_datetimes(crm):
    call, factory, _ = crm
    _pg(factory)
    with factory.begin() as c:
        _insert(c, 'clients', id=70, tenant_id=1, created_by=1, name='Shape client')
        _insert(c, 'leads', id=70, tenant_id=1, created_by=1, name='Shape lead')
        _insert(c, 'projects', id=70, tenant_id=1, created_by=1, client_id=70,
                project_name='Shape project', project_status='pending')
        _insert(c, 'contacts', id=70, tenant_id=1, client_id=70, first_name='Shape')
        _insert(c, 'interactions', id=70, tenant_id=1, project_id=70,
                summary='Shape interaction', followup_status='pending')

    changes, _, _ = _drain(call, limit=200, user=1)
    records = {change['entity']: change['record'] for change in changes}
    for entity, record in records.items():
        parents = [key[:-5] for key in record if key.endswith('_uuid')]
        assert set(record) == set(FIELDS[entity]) | {parent + '_uuid' for parent in parents}
        assert isinstance(record['created_at'], str) and 'T' in record['created_at']
        assert isinstance(record['updated_at'], str) and 'T' in record['updated_at']
        for parent in parents:
            parent_id = record[parent + '_id']
            if parent_id is None:
                assert record[parent + '_uuid'] is None
                continue
            assert record[parent + '_uuid'] == _uuid(factory, parent + 's', parent_id)
    assert records['interactions']['followup_status'] == 'pending'
    assert records['interactions']['project_uuid'] == _uuid(factory, 'projects', 70)
    # Creation-only lead conversion fields must not leak into client records.
    assert 'source_lead_id' not in records['clients']
    assert 'converted_on' not in records['clients']


def test_project_owned_contact_carries_project_uuid_and_leaves_on_access_loss(crm):
    call, factory, _ = crm
    _pg(factory)
    with factory.begin() as c:
        _insert(c, 'projects', id=920, tenant_id=1, created_by=1, assigned_to=3,
                project_name='Owned project', project_status='pending')
        _insert(c, 'contacts', id=920, tenant_id=1, project_id=920,
                first_name='Pat', last_name='Lee', is_primary=True)

    changes, cursor, _ = _drain(call, limit=200, user=3)
    project_key = _pair(factory, 'projects', 920)
    contact_key = _pair(factory, 'contacts', 920)
    contact = next(change for change in changes
                   if (change['entity'], change['uuid']) == contact_key)
    assert contact['op'] == 'upsert'
    assert project_key in set(_pairs(changes))
    assert contact['record']['project_id'] == 920
    assert contact['record']['project_uuid'] == _uuid(factory, 'projects', 920)
    assert contact['record']['is_primary'] is True
    assert contact['record']['client_id'] is None and contact['record']['lead_id'] is None

    with factory.begin() as c:
        c.execute(text('UPDATE projects SET assigned_to=1 WHERE id=920'))
    changes, _, _ = _drain(call, cursor=cursor, limit=200, user=3)
    removed = {key for key, change in zip(_pairs(changes), changes) if change['op'] == 'remove'}
    assert removed == {project_key, contact_key}


# ---------------------------------------------------------------------------
# 2. Incremental changes
# ---------------------------------------------------------------------------
def test_incremental_changes_upsert_edits_and_creates_then_go_quiet(crm):
    call, factory, _ = crm
    _pg(factory)
    _, cursor, _ = _drain(call, limit=200, user=1)

    with factory.begin() as c:
        c.execute(text("UPDATE clients SET name='Edited after cursor' WHERE id=1"))
        _insert(c, 'clients', id=80, tenant_id=1, created_by=1, name='Created after cursor')

    changes, next_cursor, modes = _drain(call, cursor=cursor, limit=200, user=1)
    assert set(modes) == {'incremental'}
    _assert_exactly_once(changes, {_pair(factory, 'clients', 1), _pair(factory, 'clients', 80)})
    assert all(change['op'] == 'upsert' for change in changes)
    edited = next(change for change in changes if change['uuid'] == _uuid(factory, 'clients', 1))
    assert edited['record']['name'] == 'Edited after cursor'

    assert _page(call, next_cursor, limit=200, user=1)['changes'] == []


# ---------------------------------------------------------------------------
# 3. Tombstones
# ---------------------------------------------------------------------------
def test_tombstones_are_removals_for_a_user_who_could_see_them(crm):
    call, factory, _ = crm
    _pg(factory)
    _, cursor, _ = _drain(call, limit=200, user=1)
    targets = (('contacts', 1), ('accounts', 1), ('interactions', 1))
    expected = {_pair(factory, table, record_id) for table, record_id in targets}

    for table, record_id in targets:
        status, body = call('DELETE', f'/api/{table}/{record_id}')
        assert status == 200, body

    changes, _, modes = _drain(call, cursor=cursor, limit=200, user=1)
    assert set(modes) == {'incremental'}
    assert set(_pairs(changes)) == expected
    assert all(change['op'] == 'remove' and set(change) == {'entity', 'op', 'uuid'}
               for change in changes)


# ---------------------------------------------------------------------------
# 4. Trash and restore
# ---------------------------------------------------------------------------
def test_trash_and_restore_client_moves_inherited_records_out_and_back(crm):
    call, factory, _ = crm
    _pg(factory)
    with factory.begin() as c:
        _insert(c, 'clients', id=100, tenant_id=1, created_by=1, name='Trash client')
        _insert(c, 'accounts', id=100, tenant_id=1, client_id=100, account_number='TRASH-1')
        _insert(c, 'contacts', id=100, tenant_id=1, client_id=100, first_name='Trash')
        _insert(c, 'interactions', id=100, tenant_id=1, client_id=100, summary='Direct')
        _insert(c, 'projects', id=100, tenant_id=1, created_by=1, client_id=100,
                project_name='Inherited', project_status='pending')
        _insert(c, 'projects', id=101, tenant_id=1, created_by=1, client_id=100,
                assigned_to=1, project_name='Inherited assigned', project_status='pending')
        _insert(c, 'interactions', id=101, tenant_id=1, project_id=100, summary='Project 100')
        _insert(c, 'interactions', id=102, tenant_id=1, project_id=101, summary='Project 101')

    expected = {
        _pair(factory, 'clients', 100), _pair(factory, 'accounts', 100),
        _pair(factory, 'contacts', 100), _pair(factory, 'projects', 100),
        _pair(factory, 'projects', 101), _pair(factory, 'interactions', 100),
        _pair(factory, 'interactions', 101), _pair(factory, 'interactions', 102),
    }
    _, cursor, _ = _drain(call, limit=200, user=1)

    status, body = call('DELETE', '/api/clients/100')
    assert status == 200, body
    changes, cursor, _ = _drain(call, cursor=cursor, limit=200, user=1)
    assert set(_pairs(changes)) == expected
    assert all(change['op'] == 'remove' for change in changes)

    status, body = call('PUT', '/api/clients/100/restore')
    assert status == 200, body
    changes, _, _ = _drain(call, cursor=cursor, limit=200, user=1)
    assert set(_pairs(changes)) == expected
    assert all(change['op'] == 'upsert' for change in changes)


# ---------------------------------------------------------------------------
# 5. Revocation and grant
# ---------------------------------------------------------------------------
def test_reassigning_a_client_removes_children_but_not_a_direct_project(crm):
    call, factory, _ = crm
    _pg(factory)
    with factory.begin() as c:
        _insert(c, 'clients', id=200, tenant_id=1, created_by=1, assigned_to=3,
                name='Shared client')
        _insert(c, 'accounts', id=200, tenant_id=1, client_id=200, account_number='SHARE-1')
        _insert(c, 'contacts', id=200, tenant_id=1, client_id=200, first_name='Shared')
        _insert(c, 'interactions', id=200, tenant_id=1, client_id=200, summary='Shared')
        _insert(c, 'projects', id=200, tenant_id=1, created_by=1, client_id=200,
                assigned_to=3, project_name='Direct project', project_status='pending')
        _insert(c, 'interactions', id=201, tenant_id=1, project_id=200, summary='Direct project call')

    inherited = {
        _pair(factory, 'clients', 200), _pair(factory, 'accounts', 200),
        _pair(factory, 'contacts', 200), _pair(factory, 'interactions', 200),
    }
    direct = {_pair(factory, 'projects', 200), _pair(factory, 'interactions', 201)}
    _, cursor, _ = _drain(call, limit=200, user=3)

    status, body = call('PUT', '/api/clients/200/assign', {'assigned_to': 1}, user=1)
    assert status == 200, body
    changes, cursor, _ = _drain(call, cursor=cursor, limit=200, user=3)
    removed = {key for key, change in zip(_pairs(changes), changes) if change['op'] == 'remove'}
    assert removed == inherited
    # The still-visible direct project is refreshed via its client's access marker.
    assert direct <= set(_pairs(changes))

    status, body = call('PUT', '/api/clients/200/assign', {'assigned_to': 3}, user=1)
    assert status == 200, body
    changes, _, _ = _drain(call, cursor=cursor, limit=200, user=3)
    restored = {(change['entity'], change['uuid']): change['op'] for change in changes}
    assert all(restored.get(key) == 'upsert' for key in inherited)


# ---------------------------------------------------------------------------
# 6. Archive
# ---------------------------------------------------------------------------
def test_archiving_a_project_removes_it_and_its_interactions(crm):
    call, factory, _ = crm
    _pg(factory)
    with factory.begin() as c:
        _insert(c, 'projects', id=300, tenant_id=1, created_by=1, client_id=1,
                project_name='To archive', project_status='pending')
        _insert(c, 'interactions', id=300, tenant_id=1, project_id=300, summary='Archived call')

    expected = {_pair(factory, 'projects', 300), _pair(factory, 'interactions', 300)}
    _, cursor, _ = _drain(call, limit=200, user=1)
    with factory.begin() as c:
        c.execute(text('UPDATE projects SET archived_at=:at, archived_by=1 WHERE id=300'),
                  {'at': datetime.utcnow()})

    changes, _, _ = _drain(call, cursor=cursor, limit=200, user=1)
    assert set(_pairs(changes)) == expected
    assert all(change['op'] == 'remove' for change in changes)


# ---------------------------------------------------------------------------
# 7. Permanent deletes
# ---------------------------------------------------------------------------
def test_purge_records_deletes_on_all_six_tables_and_feeds_removals(crm):
    call, factory, _ = crm
    _pg(factory)
    from app.services.purge import PurgeService

    with factory() as db:
        db.add_all([
            Client(id=400, tenant_id=1, created_by=1, name='Purged client'),
            Account(id=400, tenant_id=1, client_id=400, account_number='PURGE-1'),
            Contact(id=400, tenant_id=1, client_id=400, first_name='Purged contact'),
            Interaction(id=400, tenant_id=1, client_id=400, summary='Purged interaction'),
            Lead(id=400, tenant_id=1, created_by=1, name='Purged lead'),
            Contact(id=401, tenant_id=1, lead_id=400, first_name='Purged lead contact'),
            Interaction(id=401, tenant_id=1, lead_id=400, summary='Purged lead interaction'),
            Project(id=400, tenant_id=1, created_by=1, project_name='Purged project',
                    project_status='pending'),
            Interaction(id=402, tenant_id=1, project_id=400, summary='Purged project interaction'),
        ])
        db.commit()
        uuids = {(table, str(value)) for table, value in db.execute(text(
            "SELECT 'clients', uuid FROM clients WHERE id=400 UNION ALL "
            "SELECT 'accounts', uuid FROM accounts WHERE id=400 UNION ALL "
            "SELECT 'contacts', uuid FROM contacts WHERE id=400 UNION ALL "
            "SELECT 'interactions', uuid FROM interactions WHERE id=400 UNION ALL "
            "SELECT 'leads', uuid FROM leads WHERE id=400 UNION ALL "
            "SELECT 'contacts', uuid FROM contacts WHERE id=401 UNION ALL "
            "SELECT 'interactions', uuid FROM interactions WHERE id=401 UNION ALL "
            "SELECT 'projects', uuid FROM projects WHERE id=400 UNION ALL "
            "SELECT 'interactions', uuid FROM interactions WHERE id=402")).all()}
        db.commit()

    _, cursor, _ = _drain(call, limit=200, user=1)
    targets = (('clients', 400), ('accounts', 400), ('contacts', 400),
               ('interactions', 400), ('leads', 400), ('contacts', 401),
               ('interactions', 401), ('projects', 400), ('interactions', 402))
    deleted = datetime.utcnow()
    with factory.begin() as c:
        for table, record_id in targets:
            c.execute(text(f'UPDATE {table} SET deleted_at=:at, deleted_by=1 WHERE id=:id'),
                      {'at': deleted, 'id': record_id})
    with factory() as db:
        for resource, record_id in (('clients', 400), ('leads', 400), ('projects', 400)):
            assert PurgeService(db, ADMIN).purge(resource, [record_id])['deleted_ids'] == [record_id]
            db.commit()

    with factory() as db:
        for table, record_id in targets:
            assert _count(factory, table, record_id) == 0, (table, record_id)
        purged = {(row.entity, str(row.record_uuid)) for row in
                  db.execute(text('SELECT entity, record_uuid FROM sync_purges')).all()}
    assert uuids <= purged
    assert all(entity in ENTITIES for entity, _ in purged)
    # All six tables are covered, including interactions deleted via three parents.
    assert {entity for entity, _ in purged} == set(ENTITIES)

    changes, _, _ = _drain(call, cursor=cursor, limit=200, user=1)
    assert set(_pairs(changes)) == uuids
    assert all(change['op'] == 'remove' for change in changes)


def test_reused_purge_uuid_is_reported_as_a_current_record_not_a_removal(crm):
    call, factory, _ = crm
    _pg(factory)
    from app.services.purge import PurgeService

    with factory() as db:
        db.add(Client(id=500, tenant_id=1, created_by=1, name='Reused UUID',
                      deleted_at=datetime.utcnow(), deleted_by=1))
        db.commit()
        original = str(db.execute(text('SELECT uuid FROM clients WHERE id=500')).scalar_one())

    _, cursor, _ = _drain(call, limit=200, user=1)
    with factory() as db:
        assert PurgeService(db, ADMIN).purge('clients', [500])['deleted_ids'] == [500]
        db.commit()
    with factory.begin() as c:
        c.execute(text(
            'INSERT INTO clients (id, tenant_id, created_by, uuid, name) '
            'VALUES (501, 1, 1, :uuid, \'Same uuid, new record\')'), {'uuid': original})

    with factory() as db:
        assert db.execute(text('SELECT count(*) FROM sync_purges WHERE record_uuid=:uuid'),
                          {'uuid': original}).scalar_one() == 1

    changes, _, _ = _drain(call, cursor=cursor, limit=200, user=1)
    matching = [change for change in changes if change['uuid'] == original]
    assert len(matching) == 1, matching
    assert matching[0]['op'] == 'upsert'
    assert matching[0]['record']['id'] == 501


def _purge_client_and_cursor(call, factory, record_id):
    """Issue a user-3 cursor, then permanently delete one of their clients."""
    from app.services.purge import PurgeService

    with factory() as db:
        db.add(Client(id=record_id, tenant_id=1, created_by=3, name='Purge rule source'))
        db.commit()
        original = str(db.execute(text('SELECT uuid FROM clients WHERE id=:id'),
                                  {'id': record_id}).scalar_one())
    _, cursor, _ = _drain(call, limit=200, user=3)
    with factory.begin() as c:
        c.execute(text('UPDATE clients SET deleted_at=:at, deleted_by=3 WHERE id=:id'),
                  {'at': datetime.utcnow(), 'id': record_id})
    with factory() as db:
        assert PurgeService(db, ADMIN).purge('clients', [record_id])['deleted_ids'] == [record_id]
        db.commit()
    return original, cursor


@pytest.mark.parametrize('replacement', ['visible_same', 'hidden_same', 'visible_other'])
def test_purge_skip_only_applies_to_a_visible_same_entity_replacement(crm, replacement):
    call, factory, _ = crm
    _pg(factory)
    original, cursor = _purge_client_and_cursor(call, factory, 600)

    if replacement == 'visible_same':
        with factory.begin() as c:
            _insert(c, 'clients', id=601, tenant_id=1, created_by=3, uuid=original,
                    name='Visible replacement')
        expected = {('clients', original): 'upsert'}
    elif replacement == 'hidden_same':
        with factory.begin() as c:
            _insert(c, 'clients', id=601, tenant_id=1, created_by=2, uuid=original,
                    name='Hidden replacement')
        expected = {('clients', original): 'remove'}
    else:
        with factory.begin() as c:
            _insert(c, 'leads', id=601, tenant_id=1, created_by=3, uuid=original,
                    name='Other entity replacement')
        expected = {('clients', original): 'remove', ('leads', original): 'upsert'}

    changes, _, _ = _drain(call, cursor=cursor, limit=200, user=3)
    got = {}
    for change in changes:
        if change['uuid'] != original:
            continue
        key = (change['entity'], change['uuid'])
        assert key in expected, (replacement, change)
        assert key not in got, (replacement, got)
        got[key] = change['op']
    assert got == expected, (replacement, got)


def test_archive_payload_omits_sync_columns_and_marker_updates_keep_fingerprint(archive):
    call, factory, app = archive
    _pg(factory)
    signer = URLSafeTimedSerializer(app.config['SECRET_KEY'], salt='asfi-project-archive-v1')
    status, body = call('GET', '/api/owner/project-archive/preview', user=4)
    assert status == 200, body
    preview = json.loads(body)
    assert preview['projects']
    assert all(not any(key.startswith('sync_') for key in project)
               for project in preview['projects'])
    before = signer.loads(preview['preview_token'])['fingerprint']

    with factory.begin() as c:
        c.execute(text('UPDATE projects SET sync_change_xid=sync_change_xid+1 WHERE id IN (1, 10)'))

    status, body = call('GET', '/api/owner/project-archive/preview', user=4)
    assert status == 200, body
    after = signer.loads(json.loads(body)['preview_token'])['fingerprint']
    assert after == before

    status, body = call('POST', '/api/owner/project-archive/archive',
                        {'preview_token': preview['preview_token'], 'confirmation': 'ARCHIVE 2'},
                        user=4)
    assert status == 200, body
    status, body = call('GET', '/api/owner/project-archive', user=4)
    assert status == 200, body
    listing = json.loads(body)
    assert listing['projects']
    assert all(not any(key.startswith('sync_') for key in project)
               for project in listing['projects'])


# ---------------------------------------------------------------------------
# 8. Concurrency: in-flight write crosses the cursor's base
# ---------------------------------------------------------------------------
def test_write_started_before_base_but_committed_after_page_arrives_next_round(crm):
    call, factory, _ = crm
    _pg(factory)
    with factory() as writer:
        new_uuid = writer.execute(text(
            'INSERT INTO clients (tenant_id, created_by, name) VALUES (1, 1, :name) '
            'RETURNING uuid'), {'name': 'Late commit'}).scalar_one()
        writer.flush()
        # Deliberately still uncommitted while the feed takes its snapshot.
        first = _page(call, limit=200, user=1)
        assert str(new_uuid) not in _pairs(first['changes'])
        base = _cursor_payload(first['cursor'])['b']
        writer.commit()

    with factory() as db:
        marker = db.execute(text('SELECT sync_change_xid FROM clients WHERE uuid=:uuid'),
                            {'uuid': new_uuid}).scalar_one()
    assert marker >= base, (marker, base)

    changes, _, modes = _drain(call, cursor=first['cursor'], limit=200, user=1)
    assert set(modes) == {'incremental'}
    assert (('clients', str(new_uuid)) in set(_pairs(changes)))
    assert next(change for change in changes
                if change['uuid'] == str(new_uuid))['op'] == 'upsert'


# ---------------------------------------------------------------------------
# 9. Noise
# ---------------------------------------------------------------------------
def test_invisible_inserts_and_edits_emit_nothing(crm):
    call, factory, _ = crm
    _pg(factory)
    with factory.begin() as c:
        _insert(c, 'clients', id=600, tenant_id=1, created_by=2, name='Invisible edit')
        _insert(c, 'clients', id=50, tenant_id=1, created_by=3, name='Visible control')
    _, cursor, _ = _drain(call, limit=200, user=3)

    with factory.begin() as c:
        c.execute(text("UPDATE clients SET name='Still invisible' WHERE id=600"))
        _insert(c, 'clients', id=601, tenant_id=1, created_by=2, name='Invisible insert')
        c.execute(text("UPDATE clients SET name='Visible changed' WHERE id=50"))

    changes, _, _ = _drain(call, cursor=cursor, limit=200, user=3)
    assert set(_pairs(changes)) == {_pair(factory, 'clients', 50)}
    assert all(change['op'] == 'upsert' for change in changes)


# ---------------------------------------------------------------------------
# 10. Cursor security and limits
# ---------------------------------------------------------------------------
def test_bad_cursors_and_limits_are_refused(crm):
    call, factory, app = crm
    _pg(factory)
    cursor = _page(call, limit=200, user=1)['cursor']
    body, signature = cursor.split('.')
    # Change the signed bytes, not base64url padding bits: a trailing-character
    # flip can decode to the same signature and remain valid.
    tampered = ('a' if body[0] != 'a' else 'b') + body[1:] + '.' + signature

    status, payload = _request(call, cursor=tampered)
    assert status == 400 and payload['code'] == 'invalid_cursor', payload
    # 'eB' decodes like canonical 'eA' but leaves non-zero padding bits.
    status, payload = _request(call, cursor='eB.' + signature)
    assert status == 400 and payload['code'] == 'invalid_cursor', payload
    for user in (2, 3):
        status, payload = _request(call, cursor=cursor, user=user)
        assert status == 400 and payload['code'] == 'invalid_cursor', (user, payload)
    for limit in ('0', '-1', '501', 'abc', '1.5', '１', '0500x'):
        status, payload = _request(call, limit=limit)
        assert status == 400 and payload['code'] == 'invalid_limit', (limit, payload)
    status, payload = _request(call, user=None)
    assert status == 401, payload


def test_cursor_generation_change_is_refused(crm, monkeypatch):
    call, factory, _ = crm
    _pg(factory)
    cursor = _page(call)['cursor']
    monkeypatch.setenv('SYNC_FEED_GENERATION', '2')
    status, payload = _request(call, cursor=cursor)
    assert status == 400 and payload['code'] == 'invalid_cursor', payload


def test_cursor_carries_purge_epoch_and_lower_watermark(crm):
    call, factory, app = crm
    _pg(factory)
    with factory() as db:
        epoch = db.execute(text("SELECT to_regclass('sync_purges')::oid::bigint")).scalar_one()

    first = _page(call, limit=1)
    full = _cursor_payload(first['cursor'])
    assert full['m'] == 'full'
    assert full['e'] == epoch
    assert full['w'] is None  # nothing has been delivered incrementally yet

    cursor = first['cursor']
    while True:
        page = _page(call, cursor=cursor, limit=200)
        cursor = page['cursor']
        if not page['has_more']:
            break
    incremental = _cursor_payload(cursor)
    assert incremental['m'] == 'inc'
    assert incremental['e'] == epoch
    assert incremental['w'] == incremental['i']
    assert incremental['s'] is not None


def test_purge_epoch_mismatch_requires_resync(crm):
    call, factory, app = crm
    _pg(factory)
    cursor = _page(call, limit=200)['cursor']
    payload = _cursor_payload(cursor)
    changed = encode_cursor(dict(payload, e=payload['e'] + 1), app.config['SECRET_KEY'])

    status, body = _request(call, cursor=changed)
    assert status == 410 and body['code'] == 'resync_required', body


def test_partial_incremental_round_checks_lower_watermark_on_every_page(crm):
    call, factory, app = crm
    _pg(factory)
    now = int(time.time())
    old = now - 86400
    future = now + 90 * 86400
    secret = app.config['SECRET_KEY']

    with factory.begin() as c:
        _insert(c, 'clients', id=800, tenant_id=1, created_by=1, name='Before full sync')
    cursor = None
    while True:
        page = _service_page(factory, ADMIN, secret, cursor=cursor, limit=200, now=old)
        cursor = page['cursor']
        if not page['has_more']:
            break
    assert _cursor_payload(cursor)['w'] == old

    with factory.begin() as c:
        _insert(c, 'clients', id=801, tenant_id=1, created_by=1, name='Change one')
        _insert(c, 'clients', id=802, tenant_id=1, created_by=1, name='Change two')

    partial = _service_page(factory, ADMIN, secret, cursor=cursor, limit=1, now=now)
    assert partial['has_more'] is True
    state = _cursor_payload(partial['cursor'])
    assert state['m'] == 'inc'
    assert state['w'] == old  # the lower watermark is not refreshed mid-round
    assert state['i'] == now
    assert state['b'] > 0
    assert state['e'] == _cursor_payload(cursor)['e']

    # The new base and issue time are young; only the lower watermark is stale.
    with factory() as db:
        with pytest.raises(ResyncRequired):
            SyncFeedService(db, ADMIN).page(partial['cursor'], secret=secret, now=future)


def test_old_and_admin_flag_cursors_require_resync(crm):
    call, factory, app = crm
    _pg(factory)
    # Direct service cases keep the legacy device_id=None cursor shape.
    cursor = _service_page(factory, ADMIN, app.config['SECRET_KEY'], limit=200)['cursor']
    issued = _cursor_payload(cursor)['i']
    future = issued + (CURSOR_MAX_AGE_DAYS + 1) * 86400

    with factory() as db:
        with pytest.raises(ResyncRequired):
            SyncFeedService(db, ADMIN).page(cursor, secret=app.config['SECRET_KEY'], now=future)

    payload = _cursor_payload(cursor)
    assert payload['a'] is True
    flipped = encode_cursor(dict(payload, a=False), app.config['SECRET_KEY'])
    with factory() as db:
        with pytest.raises(ResyncRequired, match='permissions changed'):
            SyncFeedService(db, ADMIN).page(flipped, secret=app.config['SECRET_KEY'])

    unknown = encode_cursor(dict(payload, v=payload['v'] + 1), app.config['SECRET_KEY'])
    with factory() as db:
        with pytest.raises(ResyncRequired):
            SyncFeedService(db, ADMIN).page(unknown, secret=app.config['SECRET_KEY'])


# ---------------------------------------------------------------------------
# 11. Markers
# ---------------------------------------------------------------------------
MARKER_CASES = (
    (9100, 'clients', {'created_by': 1, 'name': 'Marker'}, 'name', 'Edited', 'assigned_to', 3),
    (9101, 'leads', {'created_by': 1, 'name': 'Marker'}, 'name', 'Edited', 'assigned_to', 3),
    (9102, 'projects', {'created_by': 1, 'project_name': 'Marker', 'project_status': 'pending'},
     'project_name', 'Edited', 'archived_at', datetime.utcnow()),
    (9103, 'accounts', {'client_id': 1, 'account_number': 'MARKER'},
     'account_name', 'Edited', 'deleted_at', datetime.utcnow()),
    (9104, 'contacts', {'client_id': 1, 'first_name': 'Marker'},
     'last_name', 'Edited', 'deleted_at', datetime.utcnow()),
    (9105, 'interactions', {'client_id': 1, 'summary': 'Marker'},
     'outcome', 'Edited', 'deleted_at', datetime.utcnow()),
)


@pytest.mark.parametrize('record_id,table,required,content_field,content,access_field,access',
                         MARKER_CASES)
def test_markers_ignore_caller_values_and_separate_content_from_access(
        crm, record_id, table, required, content_field, content, access_field, access):
    _, factory, _ = crm
    _pg(factory)

    with factory.begin() as c:
        _insert(c, table, id=record_id, tenant_id=1, sync_change_xid=111,
                sync_access_xid=222, **required)
        row = c.execute(text(
            f'SELECT sync_change_xid, sync_access_xid FROM {table} WHERE id=:id'),
            {'id': record_id}).mappings().one()
    assert row['sync_change_xid'] not in (111, 222) and row['sync_change_xid'] > 0
    assert row['sync_access_xid'] == 0
    inserted_change = row['sync_change_xid']

    with factory.begin() as c:
        row = c.execute(text(
            f'UPDATE {table} SET {content_field}=:value, sync_change_xid=111, sync_access_xid=222 '
            'WHERE id=:id RETURNING sync_change_xid, sync_access_xid'),
            {'value': content, 'id': record_id}).mappings().one()
    assert row['sync_change_xid'] not in (111, 222)
    assert row['sync_change_xid'] > inserted_change
    assert row['sync_access_xid'] == 0
    content_change = row['sync_change_xid']

    with factory.begin() as c:
        row = c.execute(text(
            f'UPDATE {table} SET {access_field}=:value, sync_change_xid=111, sync_access_xid=222 '
            'WHERE id=:id RETURNING sync_change_xid, sync_access_xid'),
            {'value': access, 'id': record_id}).mappings().one()
    assert row['sync_change_xid'] > content_change
    assert row['sync_change_xid'] not in (111, 222)
    assert row['sync_access_xid'] == row['sync_change_xid']


@pytest.mark.parametrize('table,parent_field,record_id',
                         (('clients', 'client_id', 9210), ('leads', 'lead_id', 9211)))
def test_primary_contact_change_moves_parent_change_marker(crm, table, parent_field, record_id):
    _, factory, _ = crm
    _pg(factory)
    with factory.begin() as c:
        _insert(c, table, id=record_id, tenant_id=1, created_by=1, name='Marker parent')
        _insert(c, 'contacts', id=record_id, tenant_id=1, first_name='Primary',
                is_primary=True, **{parent_field: record_id})

    def marker():
        with factory() as db:
            return db.execute(text(f'SELECT sync_change_xid FROM {table} WHERE id=:id'),
                              {'id': record_id}).scalar_one()

    before = marker()
    with factory.begin() as c:
        c.execute(text('UPDATE contacts SET last_name=:last WHERE id=:id'),
                  {'last': 'Changed', 'id': record_id})
    assert marker() > before


def test_contact_change_moves_every_pointing_project_marker(crm):
    _, factory, _ = crm
    _pg(factory)
    project_ids = (9220, 9221)
    with factory.begin() as c:
        _insert(c, 'clients', id=9220, tenant_id=1, created_by=1, name='Contact owner')
        _insert(c, 'contacts', id=9220, tenant_id=1, client_id=9220,
                first_name='Shared', last_name='Person')
        for project_id in project_ids:
            _insert(c, 'projects', id=project_id, tenant_id=1, created_by=1, client_id=9220,
                    project_name=f'Pointer {project_id}', project_status='pending',
                    primary_contact_id=9220)

    def markers():
        with factory() as db:
            return dict(db.execute(text('SELECT id, sync_change_xid FROM projects WHERE id IN (9220, 9221)')).all())

    before = markers()
    assert set(before) == set(project_ids)
    with factory.begin() as c:
        c.execute(text("UPDATE contacts SET last_name='Changed' WHERE id=9220"))
    after = markers()
    assert set(after) == set(project_ids)
    assert all(after[project_id] > before[project_id] for project_id in project_ids)


@pytest.mark.parametrize('table', ENTITIES)
def test_updating_a_record_identity_is_refused(crm, table):
    _, factory, _ = crm
    _pg(factory)
    attempts = ((f'UPDATE {table} SET tenant_id=2 WHERE id=1', {}),
                (f'UPDATE {table} SET uuid=:uuid WHERE id=1', {'uuid': uuid4()}))
    for statement, params in attempts:
        with factory() as db:
            with pytest.raises(DBAPIError) as denied:
                db.execute(text(statement), params)
            assert denied.value.orig.pgcode == '23514'
            assert 'cannot change tenant or uuid' in str(denied.value)
            db.rollback()


# ---------------------------------------------------------------------------
# 12. Restricted runtime role and RLS
# ---------------------------------------------------------------------------
@pytest.mark.isolation
def test_feed_and_purge_table_work_under_the_restricted_runtime_role(crm, rls_runtime):
    _, _, app = crm
    factory, admin, schema = rls_runtime
    from app.services.database_context import bind_principal
    from ops.isolation_contract import verify_isolation_contract

    with factory() as db:
        result = SyncFeedService(db, ADMIN).page(secret=app.config['SECRET_KEY'])
    assert result['mode'] == 'full'
    assert ('clients', _uuid(admin, 'clients', 2)) not in set(_pairs(result['changes']))
    assert ('clients', _uuid(admin, 'clients', 1)) in set(_pairs(result['changes']))

    purge_ids = [uuid4(), uuid4()]
    with admin() as db:
        db.execute(text(
            'INSERT INTO sync_purges (tenant_id, entity, record_uuid, purged_xid, purged_at) '
            'VALUES (1, \'clients\', :one, 1, now()), '
            '(2, \'clients\', :two, 1, now())'),
            {'one': purge_ids[0], 'two': purge_ids[1]})
        db.commit()

    with factory() as db:
        bind_principal(db, Principal(1, 1, frozenset({'admin'})))
        assert db.execute(text('SELECT record_uuid FROM sync_purges')).scalars().all() == [purge_ids[0]]
    with factory() as tenant_two:
        bind_principal(tenant_two, Principal(2, 2, frozenset({'admin'})))
        assert tenant_two.execute(text('SELECT record_uuid FROM sync_purges')).scalars().all() == \
            [purge_ids[1]]

    with factory() as db:
        bind_principal(db, Principal(1, 1, frozenset({'admin'})))
        for statement, params in (
            ('INSERT INTO sync_purges (tenant_id, entity, record_uuid, purged_xid, purged_at) '
             "VALUES (1, 'clients', :uuid, 1, now())", {'uuid': uuid4()}),
            ("UPDATE sync_purges SET entity='leads'", {}),
            ('DELETE FROM sync_purges', {}),
        ):
            with db.begin_nested() as savepoint:
                with pytest.raises(DBAPIError) as denied:
                    db.execute(text(statement), params)
                assert denied.value.orig.pgcode == '42501'
                savepoint.rollback()

    with admin() as db:
        sequence = db.execute(text('SELECT pg_get_serial_sequence(:table, :column)'),
                              {'table': f'{schema}.sync_purges', 'column': 'id'}).scalar_one()
        assert not db.execute(text(
            "SELECT has_sequence_privilege(:role, :sequence, 'USAGE, UPDATE')"),
            {'role': os.environ['SECURITY_TEST_ROLE'], 'sequence': sequence}).scalar_one()

    # A purge record can still only come from a real delete, written by the
    # SECURITY DEFINER trigger even though the runtime role cannot insert one.
    deleted_uuid = uuid4()
    with admin() as db:
        db.execute(text(
            'INSERT INTO clients (id, tenant_id, created_by, uuid, name) '
            "VALUES (700, 1, 1, :uuid, 'Runtime delete')"), {'uuid': deleted_uuid})
        db.commit()
    with factory() as db:
        bind_principal(db, Principal(1, 1, frozenset({'admin'})))
        assert db.execute(text('DELETE FROM clients WHERE id=700')).rowcount == 1
        db.commit()
    with admin() as db:
        rows = db.execute(text(
            'SELECT entity, record_uuid FROM sync_purges WHERE record_uuid=:uuid'),
            {'uuid': deleted_uuid}).all()
    assert [tuple(row) for row in rows] == [('clients', deleted_uuid)]

    with admin() as db:
        db.execute(text(f'SET LOCAL search_path="{schema}"'))
        checked = verify_isolation_contract(db.connection(), schema,
                                            os.environ['SECURITY_TEST_ROLE'])
    assert checked['role_restricted'] and checked['policies'] >= 1


@pytest.mark.isolation
def test_purge_definer_cannot_be_executed_or_bound_by_runtime_but_delete_records(crm, rls_runtime):
    _, _, _ = crm
    factory, admin, schema = rls_runtime
    from app.services.database_context import bind_principal
    role = os.environ['SECURITY_TEST_ROLE']

    with admin() as db:
        quote = db.connection().dialect.identifier_preparer.quote
        function = f'{quote(schema)}.{quote("sync_record_purge")}()'
        assert not db.execute(text(
            "SELECT has_function_privilege(:role, :function, 'EXECUTE')"),
            {'role': role, 'function': function}).scalar_one()

    with factory() as db:
        bind_principal(db, Principal(1, 1, frozenset({'admin'})))
        quote = db.connection().dialect.identifier_preparer.quote
        function = f'{quote(schema)}.{quote("sync_record_purge")}()'
        db.execute(text('CREATE TEMP TABLE purge_bind_probe (tenant_id integer, uuid uuid) '
                        'ON COMMIT DROP'))
        with db.begin_nested() as savepoint:
            with pytest.raises(DBAPIError) as denied:
                db.execute(text(
                    f'CREATE TRIGGER purge_bind AFTER DELETE ON purge_bind_probe '
                    f'FOR EACH ROW EXECUTE FUNCTION {function}'))
            assert denied.value.orig.pgcode == '42501'
            savepoint.rollback()

    deleted_uuid = uuid4()
    with admin() as db:
        db.execute(text(
            'INSERT INTO clients (id, tenant_id, created_by, uuid, name) '
            "VALUES (701, 1, 1, :uuid, 'Runtime delete proof')"), {'uuid': deleted_uuid})
        db.commit()
    with factory() as db:
        bind_principal(db, Principal(1, 1, frozenset({'admin'})))
        assert db.execute(text('DELETE FROM clients WHERE id=701')).rowcount == 1
        db.commit()
    with admin() as db:
        rows = db.execute(text(
            'SELECT entity, record_uuid FROM sync_purges WHERE record_uuid=:uuid'),
            {'uuid': deleted_uuid}).all()
    assert [tuple(row) for row in rows] == [('clients', deleted_uuid)]


# ---------------------------------------------------------------------------
# 14. Retention
# ---------------------------------------------------------------------------
def test_prune_only_removes_old_purge_records_and_dry_run_deletes_nothing(crm):
    call, factory, _ = crm
    _pg(factory)
    now = datetime.utcnow()
    cutoff = now - timedelta(days=prune_tombstones.RETENTION_DAYS)
    values = [uuid4(), uuid4(), uuid4()]
    with factory.begin() as c:
        for record_uuid, purged_at in zip(values, (cutoff, cutoff + timedelta(minutes=1),
                                                  cutoff - timedelta(minutes=1))):
            c.execute(text(
                'INSERT INTO sync_purges (tenant_id, entity, record_uuid, purged_xid, purged_at) '
                'VALUES (1, \'clients\', :uuid, 1, :at)'),
                {'uuid': record_uuid, 'at': purged_at})

    with factory() as db:
        counts = prune_tombstones.prune(db.connection(), now, apply=False)
        assert counts == {'interactions': 0, 'contacts': 0, 'accounts': 0, 'sync_purges': 1,
                          'write_receipts': 0}
        db.commit()
        assert db.execute(text('SELECT count(*) FROM sync_purges')).scalar_one() == 3

        assert prune_tombstones.prune(db.connection(), now, apply=True) == counts
        db.commit()
        remaining = set(db.execute(text('SELECT record_uuid FROM sync_purges')).scalars())
    assert remaining == {values[0], values[1]}


# ---------------------------------------------------------------------------
# 15. Connector verifier
# ---------------------------------------------------------------------------
def test_verifier_ignores_tombstones_and_tears_down_purges():
    source = remote_source()
    match = re.search(r'def live_only\(c, table\):\n(.*?)\n\nwith engine\.begin', source, re.S)
    assert match, 'the tombstone-aware fixture lookup is missing'
    namespace = {'text': text}
    exec('def live_only(c, table):\n' + match.group(1), namespace)  # noqa: S102

    class Connection:
        def __init__(self, present):
            self.present = present

        def execute(self, _statement, _params=None):
            present = self.present
            return type('Result', (), {'scalar_one': lambda _self: present})()

    assert namespace['live_only'](Connection(True), 'interactions') == ' AND deleted_at IS NULL'
    assert namespace['live_only'](Connection(False), 'interactions') == ''
    assert "to_regclass('public.sync_purges')" in source
    assert 'DELETE FROM public.sync_purges WHERE tenant_id=:t' in source


# ---------------------------------------------------------------------------
# 16. Response caching
# ---------------------------------------------------------------------------
def test_every_handled_feed_response_is_private(crm):
    call, factory, app = crm
    _pg(factory)
    cursor = _page(call, limit=200)['cursor']
    payload = _cursor_payload(cursor)
    stale = encode_cursor(dict(payload, e=payload['e'] + 1), app.config['SECRET_KEY'])

    paths = (
        '/api/sync/changes',
        '/api/sync/changes?limit=0',
        '/api/sync/changes?cursor=bad',
        '/api/sync/changes?' + urlencode({'cursor': stale}),
    )
    for path in paths:
        response = _response(app, factory, path)
        assert response.status_code in (200, 400, 410), (path, response.status_code)
        assert response.headers['Cache-Control'] == 'no-store', path


def test_sqlite_unsupported_response_is_private(sqlite_crm):
    _, factory, app = sqlite_crm
    response = _response(app, factory, '/api/sync/changes')
    assert response.status_code == 501
    assert response.headers['Cache-Control'] == 'no-store'
