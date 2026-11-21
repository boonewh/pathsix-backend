"""F-14: mobile-only record creates and field-level conditional edits.

These cases exercise the HTTP routes against the same PostgreSQL schema and
restricted runtime role as the other mobile acceptance tests. They cover the
device/idempotency guards, validation order, the phone's ref, parents by uuid or
ref, every allowlisted edit field, conflicts and replays, access rules, value
validation and integrity-error mapping.
"""
import asyncio
import json
import sys
import time
from collections import namedtuple
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from authlib.jose import jwt
from sqlalchemy.exc import DBAPIError

from app.models import Account, Client, Contact, Device, Interaction, Lead, MobileRecordRef, Project, User, \
    WriteReceipt
from app.services.devices import credential_marker
from app.services.mobile_records import EDITABLE
from app.utils.phone_utils import clean_phone_number
from purge_fixture import crm as crm  # noqa: F401

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prune_tombstones  # noqa: E402

HEADER = 'Idempotency-Key'
REPLAY = 'Idempotent-Replayed'
Result = namedtuple('Result', 'status text headers')

PHONE_FIELDS = {'phone', 'secondary_phone', 'primary_contact_phone'}
CANONICAL_PHONE = '+15552345678'

MINIMAL = {
    'clients': {'name': 'Seed client'},
    'leads': {'name': 'Seed lead'},
    'projects': {'project_name': 'Seed project'},
    'accounts': {'account_number': 'SEED-ACC'},
    'contacts': {'first_name': 'Seed contact'},
    'interactions': {'contact_date': '2026-11-25T10:00:00', 'summary': 'Seed interaction'},
}

ROUND_TRIP = {
    'clients': {
        'name': 'Renamed client', 'contact_person': 'New person', 'contact_title': 'New title',
        'email': 'new@example.com', 'phone': '(555) 234-5678', 'phone_label': 'mobile',
        'secondary_phone': '(555) 234-5679', 'secondary_phone_label': 'home',
        'address': '12 New Street', 'city': 'Newcity', 'state': 'NC', 'zip': '12345',
        'notes': 'New notes', 'type': 'Retail', 'status': 'active',
    },
    'leads': {
        'name': 'Renamed lead', 'contact_person': 'New person', 'contact_title': 'New title',
        'email': 'new@example.com', 'phone': '(555) 234-5678', 'phone_label': 'mobile',
        'secondary_phone': '(555) 234-5679', 'secondary_phone_label': 'home',
        'address': '12 New Street', 'city': 'Newcity', 'state': 'NC', 'zip': '12345',
        'notes': 'New notes', 'type': 'Retail', 'lead_status': 'qualified', 'lead_source': 'Referral',
    },
    'projects': {
        'project_name': 'Renamed project', 'project_description': 'New description', 'type': 'Build',
        'project_status': 'in_progress', 'project_start': '2026-12-01T09:30:00',
        'project_end': '2026-12-15T17:00:00', 'project_worth': 12345.5, 'value_type': 'monthly',
        'notes': 'New notes', 'primary_contact_name': 'New contact', 'primary_contact_title': 'New title',
        'primary_contact_email': 'contact@example.com', 'primary_contact_phone': '(555) 234-5678',
        'primary_contact_phone_label': 'mobile',
    },
    'accounts': {'account_name': 'New account name', 'account_number': 'NEW-ACC', 'notes': 'New notes'},
    'contacts': {
        'first_name': 'Renamed', 'last_name': 'Contact', 'title': 'New title', 'email': 'new@example.com',
        'phone': '(555) 234-5678', 'phone_label': 'mobile', 'secondary_phone': '(555) 234-5679',
        'secondary_phone_label': 'home', 'notes': 'New notes',
    },
    'interactions': {
        'contact_date': '2026-12-01T10:00:00', 'summary': 'New summary', 'outcome': 'Won',
        'notes': 'New notes', 'follow_up': '2026-12-08T10:00:00', 'contact_person': 'New person',
        'email': 'new@example.com', 'phone': '(555) 234-5678', 'followup_status': 'completed',
    },
}

@pytest.fixture
def api(crm):
    """The shared crm fixture plus device-token and Idempotency-Key headers."""
    _, factory, app = crm
    if factory.kw['bind'].dialect.name != 'postgresql':
        pytest.skip('Requires PostgreSQL with the mobile migrations')
    devices = {}

    def device_for(user_id):
        if user_id not in devices:
            with factory() as db:
                account = db.get(User, user_id)
                device = Device(id=uuid4(), tenant_id=account.tenant_id, user_id=account.id,
                                name=f'F14 device {user_id}', platform='ios',
                                created_at=datetime.utcnow(), last_seen_at=datetime.utcnow(),
                                refresh_hash='a' * 64,
                                refresh_expires_at=datetime.utcnow() + timedelta(days=30),
                                credential_marker=credential_marker(account))
                db.add(device)
                db.commit()
                devices[user_id] = device.id
        return devices[user_id]

    def call(method, path, body=None, *, user=1, key=None, device=True):
        payload = {'sub': user, 'exp': int(time.time()) + 300, 'roles': ['admin']}
        if device:
            payload.update(did=str(device_for(user)), typ='device')
        token = jwt.encode({'alg': 'HS256'}, payload, app.config['SECRET_KEY']).decode()
        headers = {'Authorization': f'Bearer {token}'}
        if key is not None:
            headers[HEADER] = key

        async def run():
            response = await app.test_client().open(method=method, path=path, json=body, headers=headers)
            return Result(response.status_code, await response.get_data(as_text=True), dict(response.headers))

        return asyncio.run(run())

    return call, factory, app


def new_key():
    return 'f14' + uuid4().hex


def response(result, status=None):
    if status is not None:
        assert result.status == status, result.text
    return json.loads(result.text)


def header(result, name):
    return next((value for key, value in result.headers.items() if key.lower() == name.lower()), None)


def minimal_fields(entity):
    fields = dict(MINIMAL[entity])
    if entity == 'accounts':
        fields['account_number'] = 'ACC-' + uuid4().hex[:10]
    return fields


def ref_value():
    return str(uuid4())


def uuid_for(factory, model, record_id):
    with factory() as db:
        return str(db.get(model, record_id).uuid)


def parents_for(factory, entity):
    if entity in ('projects', 'accounts', 'contacts', 'interactions'):
        return {'client': {'uuid': uuid_for(factory, Client, 1)}}
    return {}


def create(api, entity, *, fields=None, parents=None, ref=None, key=None, user=1, device=True):
    call, factory, _ = api
    ref = ref or ref_value()
    key = key or new_key()
    body = {'ref': ref, 'fields': fields if fields is not None else minimal_fields(entity),
            'parents': parents_for(factory, entity) if parents is None else parents}
    result = call('POST', f'/api/mobile/records/{entity}', body, user=user, key=key, device=device)
    return body, result, key


def edit(api, entity, target, changes, base, *, key=None, user=1, device=True):
    call, _, _ = api
    key = key or new_key()
    body = {'changes': changes, 'base': base}
    result = call('PATCH', f'/api/mobile/records/{entity}/{target}', body, user=user, key=key, device=device)
    return body, result, key


def receipt(factory, key):
    with factory() as db:
        row = db.query(WriteReceipt).filter_by(request_key=key).first()
        if row is None:
            return None
        return {'status_code': row.status_code, 'response': dict(row.response)}


def ref_rows(factory, ref=None):
    with factory() as db:
        query = db.query(MobileRecordRef)
        if ref is not None:
            query = query.filter_by(ref=ref)
        return [{'tenant_id': row.tenant_id, 'entity': row.entity, 'record_id': row.record_id,
                 'ref': str(row.ref), 'created_at': row.created_at} for row in query.all()]


def count(factory, model, **filters):
    with factory() as db:
        query = db.query(model)
        for name, value in filters.items():
            query = query.filter(getattr(model, name) == value)
        return query.count()


def set_columns(factory, model, record_id, **values):
    with factory() as db:
        row = db.get(model, record_id)
        for name, value in values.items():
            setattr(row, name, value)
        db.commit()


def expected_value(entity, field, value):
    if field == 'phone' and entity == 'interactions':
        return value  # the interaction service stores this field as given
    if field in PHONE_FIELDS:
        return clean_phone_number(value)
    if entity == 'accounts' and field == 'account_number':
        return None  # replaced below by the per-case generated value
    return value


def test_device_token_and_idempotency_key_are_required(api):
    call, factory, _ = api
    _, created, _ = create(api, 'clients')
    client = response(created, 201)
    target = client['uuid']

    web = call('POST', '/api/mobile/records/clients', {'ref': ref_value(), 'fields': minimal_fields('clients'),
                                                       'parents': {}}, key=new_key(), device=False)
    assert web.status == 403, web.text
    assert response(web)['code'] == 'device_token_required'
    assert header(web, 'Cache-Control') == 'no-store'

    web_edit = call('PATCH', f'/api/mobile/records/clients/{target}',
                    {'changes': {'name': 'x'}, 'base': {'name': client['record']['name']}},
                    key=new_key(), device=False)
    assert web_edit.status == 403, web_edit.text
    assert response(web_edit)['code'] == 'device_token_required'

    for method, path, body in (
        ('POST', '/api/mobile/records/clients',
         {'ref': ref_value(), 'fields': {'name': 'No key'}, 'parents': {}}),
        ('PATCH', f'/api/mobile/records/clients/{target}',
         {'changes': {'name': 'No key'}, 'base': {'name': client['record']['name']}}),
    ):
        missing = call(method, path, body)
        assert missing.status == 400, missing.text
        assert response(missing)['code'] == 'idempotency_key_required'

    assert count(factory, Client, name='No key') == 0


def test_stateless_validation_runs_before_receipt_and_write(api):
    call, factory, _ = api
    key = new_key()
    bad = {'ref': ref_value(), 'fields': {'name': 'Bad', 'bogus': 1}, 'parents': {}}
    invalid = call('POST', '/api/mobile/records/clients', bad, key=key)
    assert invalid.status == 400, invalid.text
    assert response(invalid)['code'] == 'invalid_request'
    assert 'bogus' in response(invalid)['error']
    assert receipt(factory, key) is None

    good = {'ref': ref_value(), 'fields': {'name': 'Good after invalid'}, 'parents': {}}
    accepted = call('POST', '/api/mobile/records/clients', good, key=key)
    assert accepted.status == 201, accepted.text

    _, created, _ = create(api, 'clients')
    target = response(created, 201)['uuid']
    edit_key = new_key()
    mismatch = call('PATCH', f'/api/mobile/records/clients/{target}',
                    {'changes': {'name': 'x'}, 'base': {}}, key=edit_key)
    assert mismatch.status == 400, mismatch.text
    assert response(mismatch)['code'] == 'invalid_request'
    assert receipt(factory, edit_key) is None

    # An invalid body is rejected before the route asks for a key.
    no_key = call('PATCH', f'/api/mobile/records/clients/{target}',
                  {'changes': {'name': 'x'}, 'base': {}})
    assert no_key.status == 400, no_key.text
    assert response(no_key)['code'] == 'invalid_request'


def test_create_returns_canonical_uuid_ref_row_and_replay_ids_only(api):
    call, factory, _ = api
    phone_ref = ref_value()
    body, first, key = create(api, 'clients', ref=phone_ref)
    created = response(first, 201)
    assert set(created) == {'id', 'uuid', 'record'}
    assert type(created['id']) is int and created['uuid'] != phone_ref
    assert created['record']['id'] == created['id']
    assert created['record']['uuid'] == created['uuid']

    with factory() as db:
        assert str(db.get(Client, created['id']).uuid) == created['uuid']

    rows = ref_rows(factory, phone_ref)
    assert len(rows) == 1
    assert rows[0]['tenant_id'] == 1 and rows[0]['entity'] == 'clients'
    assert rows[0]['record_id'] == created['id'] and rows[0]['created_at']

    stored = receipt(factory, key)
    assert stored == {'status_code': 201, 'response': {'id': created['id'], 'uuid': created['uuid']}}

    replay = call('POST', '/api/mobile/records/clients', body, key=key)
    acknowledged = response(replay, 201)
    assert set(acknowledged) == {'id', 'uuid'}
    assert acknowledged == {'id': created['id'], 'uuid': created['uuid']}
    assert header(replay, REPLAY) == 'true'
    assert header(replay, 'Cache-Control') == 'no-store'
    assert count(factory, Client, name=body['fields']['name']) == 1
    assert len(ref_rows(factory, phone_ref)) == 1


def test_ref_collision_rolls_back_and_returns_409(api):
    call, factory, _ = api
    used = ref_value()
    _, first, _ = create(api, 'clients', ref=used, fields={'name': 'First owner'})
    first_id = response(first, 201)['id']

    second_key = new_key()
    collision = call('POST', '/api/mobile/records/clients',
                     {'ref': used, 'fields': {'name': 'Colliding'}, 'parents': {}}, key=second_key)
    assert collision.status == 409, collision.text
    assert response(collision)['code'] == 'ref_unavailable'
    assert receipt(factory, second_key) is None
    assert count(factory, Client, name='Colliding') == 0
    assert [row['record_id'] for row in ref_rows(factory, used)] == [first_id]


def test_ref_namespace_is_per_tenant(api):
    _, factory, _ = api
    shared = ref_value()
    _, first, _ = create(api, 'clients', ref=shared, fields={'name': 'Tenant one'})
    _, second, _ = create(api, 'clients', ref=shared, fields={'name': 'Tenant two'}, user=2)

    assert response(first, 201)['uuid'] != response(second, 201)['uuid']
    rows = ref_rows(factory, shared)
    assert len(rows) == 2 and {row['tenant_id'] for row in rows} == {1, 2}


def test_parents_resolve_by_uuid_and_ref_and_require_visibility(api):
    call, factory, _ = api
    parent_ref = ref_value()
    _, parent, _ = create(api, 'clients', ref=parent_ref, fields={'name': 'Visible parent'})
    parent_id = response(parent, 201)['id']
    parent_uuid = uuid_for(factory, Client, parent_id)

    _, by_uuid, _ = create(api, 'projects', parents={'client': {'uuid': parent_uuid}},
                           fields={'project_name': 'By uuid'})
    assert response(by_uuid, 201)['record']['client_id'] == parent_id

    _, by_ref, _ = create(api, 'projects', parents={'client': {'ref': parent_ref}},
                          fields={'project_name': 'By ref'})
    assert response(by_ref, 201)['record']['client_id'] == parent_id

    foreign_ref = ref_value()
    create(api, 'clients', ref=foreign_ref, fields={'name': 'Tenant two parent'}, user=2)
    for spec in ({'uuid': uuid_for(factory, Client, 2)}, {'ref': foreign_ref}):
        hidden = call('POST', '/api/mobile/records/projects',
                      {'ref': ref_value(), 'fields': {'project_name': 'Hidden parent'},
                       'parents': {'client': spec}}, key=new_key())
        assert hidden.status == 404, hidden.text
        assert response(hidden)['code'] == 'not_found'

    no_access = call('POST', '/api/mobile/records/projects',
                     {'ref': ref_value(), 'fields': {'project_name': 'No access'},
                      'parents': {'client': {'uuid': uuid_for(factory, Client, 1)}}},
                     key=new_key(), user=3)
    assert no_access.status == 404, no_access.text

    _, deleted_parent, _ = create(api, 'clients', fields={'name': 'Deleted parent'})
    deleted_id = response(deleted_parent, 201)['id']
    set_columns(factory, Client, deleted_id, deleted_at=datetime.utcnow(), deleted_by=1)
    gone = call('POST', '/api/mobile/records/projects',
                {'ref': ref_value(), 'fields': {'project_name': 'Deleted parent'},
                 'parents': {'client': {'uuid': uuid_for(factory, Client, deleted_id)}}}, key=new_key())
    assert gone.status == 404, gone.text


def test_integrity_error_maps_to_400_without_receipt_or_ref(api):
    call, factory, _ = api
    duplicate = 'DUP-' + uuid4().hex[:10]
    _, first, _ = create(api, 'accounts', fields={'account_number': duplicate, 'notes': 'first'})
    assert response(first, 201)['record']['account_number'] == duplicate

    second_ref = ref_value()
    second_key = new_key()
    result = call('POST', '/api/mobile/records/accounts',
                  {'ref': second_ref, 'fields': {'account_number': duplicate, 'notes': 'second'},
                   'parents': parents_for(factory, 'accounts')}, key=second_key)
    assert result.status == 400, result.text
    assert response(result)['code'] == 'invalid_request'
    assert 'conflicts with an existing one' in response(result)['error']
    assert receipt(factory, second_key) is None
    assert ref_rows(factory, second_ref) == []
    assert count(factory, Account, account_number=duplicate) == 1


def test_text_infinity_is_rejected_on_create_and_edit(api):
    _, factory, _ = api
    fields = minimal_fields('projects')
    fields['project_worth'] = 'Infinity'
    create_key = new_key()
    _, result, _ = create(api, 'projects', fields=fields, key=create_key)
    assert result.status == 400, result.text
    assert response(result)['code'] == 'invalid_request'
    assert 'project_worth must be a finite number' in response(result)['error']
    assert receipt(factory, create_key) is None

    _, created, _ = create(api, 'projects')
    initial = response(created, 201)
    target = initial['uuid']
    edit_key = new_key()
    _, result, _ = edit(api, 'projects', target, {'project_worth': 'Infinity'},
                        {'project_worth': initial['record']['project_worth']}, key=edit_key)
    assert result.status == 400, result.text
    assert response(result)['code'] == 'invalid_request'
    assert 'project_worth must be a finite number' in response(result)['error']
    assert receipt(factory, edit_key) is None
    with factory() as db:
        assert db.get(Project, initial['id']).project_worth == initial['record']['project_worth']


def test_project_status_longer_than_column_is_rejected(api):
    _, factory, _ = api
    fields = minimal_fields('projects')
    fields['project_status'] = 'x' * 21
    create_key = new_key()
    _, result, _ = create(api, 'projects', fields=fields, key=create_key)
    assert result.status == 400, result.text
    assert response(result)['code'] == 'invalid_request'
    assert 'project_status must be at most 20 characters' in response(result)['error']
    assert receipt(factory, create_key) is None

    _, created, _ = create(api, 'projects')
    initial = response(created, 201)
    target = initial['uuid']
    edit_key = new_key()
    _, result, _ = edit(api, 'projects', target, {'project_status': 'y' * 21},
                        {'project_status': initial['record']['project_status']}, key=edit_key)
    assert result.status == 400, result.text
    assert response(result)['code'] == 'invalid_request'
    assert 'project_status must be at most 20 characters' in response(result)['error']
    assert receipt(factory, edit_key) is None
    with factory() as db:
        assert db.get(Project, initial['id']).project_status == initial['record']['project_status']


def test_project_worth_string_base_is_rejected(api):
    _, factory, _ = api
    _, created, _ = create(api, 'projects')
    initial = response(created, 201)
    target = initial['uuid']
    key = new_key()
    _, result, _ = edit(api, 'projects', target, {'project_worth': 12.5},
                        {'project_worth': '0'}, key=key)
    assert result.status == 400, result.text
    assert response(result)['code'] == 'invalid_request'
    assert 'base.project_worth must be a number or null' in response(result)['error']
    assert receipt(factory, key) is None
    with factory() as db:
        assert db.get(Project, initial['id']).project_worth == initial['record']['project_worth']


@pytest.mark.parametrize('value', [float('nan'), float('inf'), True, ['x'], {'x': 1}, None],
                         ids=['nan', 'inf', 'boolean', 'list', 'object', 'null'])
def test_create_refuses_non_scalar_values_and_required_nulls(api, value):
    _, factory, _ = api
    key = new_key()
    _, result, _ = create(api, 'clients', fields={'name': value}, key=key)
    assert result.status == 400, result.text
    assert response(result)['code'] == 'invalid_request'
    assert receipt(factory, key) is None


@pytest.mark.parametrize('value', [float('nan'), -float('inf'), True, ['x'], {'x': 1}],
                         ids=['nan', 'inf', 'boolean', 'list', 'object'])
def test_edit_refuses_non_scalar_values(api, value):
    _, factory, _ = api
    _, created, _ = create(api, 'clients')
    target = response(created, 201)['uuid']
    key = new_key()
    _, result, _ = edit(api, 'clients', target, {'notes': value}, {'notes': value}, key=key)
    assert result.status == 400, result.text
    assert response(result)['code'] == 'invalid_request'
    assert receipt(factory, key) is None


def test_edit_refuses_null_in_a_required_column(api):
    _, factory, _ = api
    _, created, _ = create(api, 'clients', fields={'name': 'Required'})
    target = response(created, 201)['uuid']
    key = new_key()
    _, result, _ = edit(api, 'clients', target, {'name': None}, {'name': 'Required'}, key=key)
    assert result.status == 400, result.text
    assert response(result)['code'] == 'invalid_request'
    assert receipt(factory, key) is None


def test_explicit_null_base_and_null_change_round_trip(api):
    _, created, _ = create(api, 'clients', fields={'name': 'Null base', 'notes': 'Seed note'})
    target = response(created, 201)['uuid']

    _, cleared, _ = edit(api, 'clients', target, {'notes': None}, {'notes': 'Seed note'})
    assert response(cleared, 200)['record']['notes'] is None

    _, restored, _ = edit(api, 'clients', target, {'notes': 'Restored'}, {'notes': None})
    assert response(restored, 200)['record']['notes'] == 'Restored'


@pytest.mark.parametrize('entity', sorted(EDITABLE))
def test_every_editable_field_round_trips(api, entity):
    values = dict(ROUND_TRIP[entity])
    if entity == 'accounts':
        values['account_number'] = 'RT-' + uuid4().hex[:10]
    assert set(values) == set(EDITABLE[entity])

    _, created, _ = create(api, entity)
    initial = response(created, 201)
    base = {field: initial['record'][field] for field in values}

    _, result, _ = edit(api, entity, initial['uuid'], values, base)
    record = response(result, 200)['record']
    for field, value in values.items():
        if entity == 'accounts' and field == 'account_number':
            expected = value
        else:
            expected = expected_value(entity, field, value)
        if field == 'project_worth':
            assert float(record[field]) == pytest.approx(value), field
        else:
            assert record[field] == expected, field


def test_phone_cleaning_and_null_round_trip(api):
    _, created, _ = create(api, 'clients', fields={'name': 'Phone', 'phone': None})
    target = response(created, 201)['uuid']

    _, cleaned, _ = edit(api, 'clients', target, {'phone': '(555) 234-5678'}, {'phone': None})
    assert response(cleaned, 200)['record']['phone'] == CANONICAL_PHONE

    _, cleared, _ = edit(api, 'clients', target, {'phone': None}, {'phone': CANONICAL_PHONE})
    assert response(cleared, 200)['record']['phone'] is None


def test_conflict_rolls_back_everything_and_writes_no_receipt(api):
    call, factory, _ = api
    _, created, _ = create(api, 'clients', fields={'name': 'Original'})
    created_body = response(created, 201)
    target = created_body['uuid']
    set_columns(factory, Client, created_body['id'], name='Theirs')

    key = new_key()
    _, conflict, _ = edit(api, 'clients', target, {'name': 'Mine'}, {'name': 'Original'}, key=key)
    payload = response(conflict, 409)
    assert payload['code'] == 'edit_conflict'
    assert payload['conflicts'] == [{'field': 'name', 'base': 'Original', 'mine': 'Mine', 'theirs': 'Theirs'}]
    assert payload['record']['name'] == 'Theirs'
    assert receipt(factory, key) is None
    with factory() as db:
        assert db.get(Client, created_body['id']).name == 'Theirs'

    _, accepted, _ = edit(api, 'clients', target, {'name': 'Mine'}, {'name': 'Theirs'})
    assert response(accepted, 200)['record']['name'] == 'Mine'


def test_same_new_value_and_normalised_equal_values_do_not_conflict(api):
    _, factory, _ = api
    _, created, _ = create(api, 'clients', fields={'name': 'Old', 'phone': CANONICAL_PHONE})
    created_body = response(created, 201)
    target = created_body['uuid']
    set_columns(factory, Client, created_body['id'], name='Theirs')

    _, same, _ = edit(api, 'clients', target, {'name': 'Theirs'}, {'name': 'Old'})
    assert response(same, 200)['record']['name'] == 'Theirs'

    _, equal, _ = edit(api, 'clients', target, {'phone': '(555) 234-5678'},
                       {'phone': '+1 555 234 5678'})
    assert response(equal, 200)['record']['phone'] == CANONICAL_PHONE


def test_replays_after_later_edit_and_after_loss_of_access(api):
    call, factory, _ = api
    _, created, _ = create(api, 'clients', fields={'name': 'First'}, user=3)
    created_body = response(created, 201)
    target = created_body['uuid']

    body, first, key = edit(api, 'clients', target, {'name': 'Second'}, {'name': 'First'}, user=3)
    assert response(first, 200)['record']['name'] == 'Second'
    set_columns(factory, Client, created_body['id'], name='Third')

    replay = call('PATCH', f'/api/mobile/records/clients/{target}', body, user=3, key=key)
    acknowledged = response(replay, 200)
    assert set(acknowledged) == {'id', 'uuid'}
    assert acknowledged == {'id': created_body['id'], 'uuid': target}
    assert header(replay, REPLAY) == 'true'
    with factory() as db:
        assert db.get(Client, created_body['id']).name == 'Third'

    set_columns(factory, Client, created_body['id'], created_by=2, assigned_to=None)
    lost = call('PATCH', f'/api/mobile/records/clients/{target}', body, user=3, key=key)
    assert set(response(lost, 200)) == {'id', 'uuid'}
    assert header(lost, REPLAY) == 'true'

    denied_key = new_key()
    denied = call('PATCH', f'/api/mobile/records/clients/{target}',
                  {'changes': {'name': 'Fourth'}, 'base': {'name': 'Third'}},
                  user=3, key=denied_key)
    assert denied.status == 404, denied.text
    assert receipt(factory, denied_key) is None


def test_hidden_deleted_and_cross_tenant_edits_return_404(api):
    _, factory, _ = api
    _, created, _ = create(api, 'clients', fields={'name': 'To delete'})
    created_body = response(created, 201)
    deleted_key = new_key()
    set_columns(factory, Client, created_body['id'], deleted_at=datetime.utcnow(), deleted_by=1)
    _, deleted, _ = edit(api, 'clients', created_body['uuid'], {'name': 'x'},
                         {'name': 'To delete'}, key=deleted_key)
    assert deleted.status == 404, deleted.text
    assert response(deleted)['code'] == 'not_found'
    assert receipt(factory, deleted_key) is None

    _, parent, _ = create(api, 'clients', fields={'name': 'Account parent'})
    parent_id = response(parent, 201)['id']
    _, account, _ = create(api, 'accounts', parents={'client': {'uuid': uuid_for(factory, Client, parent_id)}})
    account_body = response(account, 201)
    set_columns(factory, Client, parent_id, deleted_at=datetime.utcnow(), deleted_by=1)
    _, hidden, _ = edit(api, 'accounts', account_body['uuid'], {'notes': 'x'}, {'notes': None})
    assert hidden.status == 404, hidden.text
    assert response(hidden)['code'] == 'not_found'

    _, foreign, _ = edit(api, 'clients', uuid_for(factory, Client, 2), {'name': 'x'},
                         {'name': 'Private client 2'})
    assert foreign.status == 404, foreign.text
    assert response(foreign)['code'] == 'not_found'


def test_server_uuid_is_immutable_and_cannot_be_supplied(api):
    call, factory, _ = api
    extra = {'ref': ref_value(), 'fields': {'name': 'No client uuid'}, 'parents': {}, 'uuid': str(uuid4())}
    rejected = call('POST', '/api/mobile/records/clients', extra, key=new_key())
    assert rejected.status == 400, rejected.text
    assert response(rejected)['code'] == 'invalid_request'

    _, created, _ = create(api, 'clients', fields={'name': 'Immutable'})
    created_body = response(created, 201)
    with factory() as db:
        row = db.get(Client, created_body['id'])
        row.uuid = uuid4()
        with pytest.raises(DBAPIError):
            db.flush()
        db.rollback()
        assert str(db.get(Client, created_body['id']).uuid) == created_body['uuid']


def test_prune_keeps_young_mobile_refs(api):
    _, factory, _ = api
    ref = ref_value()
    create(api, 'clients', ref=ref)
    young = datetime.utcnow() - timedelta(days=prune_tombstones.RETENTION_DAYS) + timedelta(hours=1)
    with factory() as db:
        db.query(MobileRecordRef).filter_by(ref=ref).one().created_at = young
        db.commit()
        prune_tombstones.prune(db.connection(), datetime.utcnow(), apply=True)
        db.commit()
    assert len(ref_rows(factory, ref)) == 1


def test_client_email_edit_compares_and_stores_through_the_contact(api):
    _, factory, _ = api
    _, created, _ = create(api, 'clients', fields={'name': 'Email parent', 'email': 'old@example.com'})
    created_body = response(created, 201)
    target, client_id = created_body['uuid'], created_body['id']
    with factory() as db:
        contact = db.query(Contact).filter_by(client_id=client_id, is_primary=True).one()
        contact_id = contact.id
        assert contact.email == 'old@example.com'

    set_columns(factory, Contact, contact_id, email='theirs@example.com')
    key = new_key()
    _, conflict, _ = edit(api, 'clients', target, {'email': 'mine@example.com'},
                          {'email': 'old@example.com'}, key=key)
    payload = response(conflict, 409)
    assert payload['code'] == 'edit_conflict'
    assert payload['conflicts'] == [
        {'field': 'email', 'base': 'old@example.com', 'mine': 'mine@example.com',
         'theirs': 'theirs@example.com'},
    ]
    assert receipt(factory, key) is None
    with factory() as db:
        assert db.get(Contact, contact_id).email == 'theirs@example.com'

    _, accepted, _ = edit(api, 'clients', target, {'email': 'mine@example.com'},
                          {'email': 'theirs@example.com'})
    assert response(accepted, 200)['id'] == client_id
    with factory() as db:
        assert db.get(Contact, contact_id).email == 'mine@example.com'
        assert db.get(Client, client_id).email == 'mine@example.com'


def test_contacts_entity_edit_of_a_primary_contact_is_refused(api):
    _, factory, _ = api
    _, created, _ = create(api, 'clients', fields={'name': 'Primary parent',
                                                   'email': 'primary@example.com'})
    created_body = response(created, 201)
    with factory() as db:
        contact = db.query(Contact).filter_by(client_id=created_body['id'], is_primary=True).one()
        contact_id, contact_uuid = contact.id, str(contact.uuid)

    key = new_key()
    _, result, _ = edit(api, 'contacts', contact_uuid, {'email': 'changed@example.com'},
                        {'email': 'primary@example.com'}, key=key)
    assert response(result, 400)['code'] == 'edit_on_parent'
    assert receipt(factory, key) is None
    with factory() as db:
        assert db.get(Contact, contact_id).email == 'primary@example.com'
