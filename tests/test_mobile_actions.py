"""F-17 (mobile step 4): lead conversion and assignment as mobile actions.

Runs against the same PostgreSQL schema and restricted runtime role as the other
mobile acceptance tests (the `api` fixture from test_mobile_records). Design:
pathsix-crm-mobile mobile-app-plan.md, step 4, revision 4.
"""
import threading
import time
from datetime import datetime

from app.models import Client, Contact, Interaction, Lead, MobileRecordRef, Project, User, WriteReceipt
from app.routes import mobile_records as mobile_records_routes
from purge_fixture import crm as crm  # noqa: F401
from test_mobile_records import api as api  # noqa: F401
from test_mobile_records import create, new_key, ref_value, response


def make_lead(api, *, user=1, name='Convert me'):
    _, result, _ = create(api, 'leads', fields={
        'name': name, 'contact_person': 'Pat Lee', 'contact_title': 'Owner', 'email': 'pat@example.com',
        'phone': '(555) 234-5678', 'city': 'Akron', 'state': 'OH', 'notes': 'Lead notes', 'type': 'Retail',
    }, user=user)
    body = response(result, 201)
    return body['id'], body['uuid']


def convert(api, lead_uuid, *, ref=None, key=None, user=1, device=True):
    call, _, _ = api
    key = key or new_key()
    body = {'ref': ref or ref_value()}
    return body, call('POST', f'/api/mobile/records/leads/{lead_uuid}/convert', body, user=user, key=key,
                      device=device), key


def assign(api, entity, target, assigned_to, base, *, key=None, user=1, device=True):
    call, _, _ = api
    key = key or new_key()
    body = {'assigned_to': assigned_to, 'base': base}
    return call('POST', f'/api/mobile/records/{entity}/{target}/assign', body, user=user, key=key,
                device=device), key


def test_conversion_creates_the_client_moves_everything_and_retires_the_lead(api):
    call, factory, _ = api
    lead_id, lead_uuid = make_lead(api)
    child = {'lead': {'uuid': lead_uuid}}
    interaction = response(create(api, 'interactions', fields={
        'contact_date': '2026-11-25T09:00:00', 'summary': 'Called'}, parents=child)[1], 201)
    project = response(create(api, 'projects', fields={'project_name': 'Lead project'}, parents=child)[1], 201)
    contact = response(create(api, 'contacts', fields={'first_name': 'Second'}, parents=child)[1], 201)

    body, result, key = convert(api, lead_uuid)
    created = response(result, 201)
    assert set(created) == {'id', 'uuid', 'record'}
    assert created['record']['name'] == 'Convert me'
    with factory() as db:
        client = db.get(Client, created['id'])
        assert (client.source_lead_id, client.status, client.type) == (lead_id, 'active', 'Retail')
        assert client.converted_on is not None
        assert (client.contact_person, client.email, client.city, client.notes) == (
            'Pat Lee', 'pat@example.com', 'Akron', 'Lead notes')
        lead = db.get(Lead, lead_id)
        assert lead.deleted_at is not None and lead.lead_status == 'won' and lead.converted_on is not None
        moved = db.get(Interaction, interaction['id'])
        assert (moved.client_id, moved.lead_id) == (client.id, None)
        moved_project = db.get(Project, project['id'])
        assert (moved_project.client_id, moved_project.lead_id) == (client.id, None)
        moved_contact = db.get(Contact, contact['id'])
        assert (moved_contact.client_id, moved_contact.lead_id) == (client.id, None)
        ref = db.query(MobileRecordRef).filter_by(ref=body['ref']).one()
        assert (ref.entity, ref.record_id) == ('clients', client.id)
        assert db.query(Client).filter_by(source_lead_id=lead_id).count() == 1

    # A retry with the same key is acknowledged with identifiers only.
    replay = call('POST', f'/api/mobile/records/leads/{lead_uuid}/convert', body, key=key)
    assert response(replay, 201) == {'id': created['id'], 'uuid': created['uuid']}
    with factory() as db:
        assert db.query(Client).filter_by(source_lead_id=lead_id).count() == 1

    # The lead is gone: a later child create naming it is refused.
    late = create(api, 'interactions', fields={'contact_date': '2026-11-25T10:00:00', 'summary': 'Late'},
                  parents=child)[1]
    assert late.status == 404, late.text


def test_a_lead_already_converted_is_refused_and_hides_a_client_the_caller_cannot_see(api):
    _, factory, _ = api
    # Ordinary user 3 owns the lead; the half-finished web conversion's client
    # belongs to the admin, so user 3 cannot see it.
    lead_id, lead_uuid = make_lead(api, user=3, name='Half converted')
    with factory() as db:
        client = Client(tenant_id=1, created_by=1, name='Made on the web', source_lead_id=lead_id)
        db.add(client)
        db.commit()
        client_uuid = str(client.uuid)
    _, result, key = convert(api, lead_uuid, user=3)
    refused = response(result, 409)
    assert refused['code'] == 'already_converted' and 'client_uuid' not in refused
    _, result, _ = convert(api, lead_uuid, user=1)
    assert response(result, 409)['client_uuid'] == client_uuid
    with factory() as db:
        lead = db.get(Lead, lead_id)
        assert lead.deleted_at is None and lead.lead_status != 'won'
        assert db.query(WriteReceipt).filter_by(request_key=key).count() == 0


def test_conversion_validation_access_and_retry_key(api):
    call, _, _ = api
    _, lead_uuid = make_lead(api)
    path = f'/api/mobile/records/leads/{lead_uuid}/convert'
    assert call('POST', path, {}, key=new_key()).status == 400
    assert call('POST', path, {'ref': ref_value(), 'fields': {}}, key=new_key()).status == 400
    assert call('POST', path, {'ref': 'not-a-uuid'}, key=new_key()).status == 400
    assert call('POST', path, {'ref': ref_value()}).status == 400  # no Idempotency-Key
    assert call('POST', path, {'ref': ref_value()}, key=new_key(), device=False).status == 403
    assert call('POST', '/api/mobile/records/leads/not-a-uuid/convert', {'ref': ref_value()}, key=new_key()).status == 404
    # Another tenant's admin cannot see the lead.
    assert convert(api, lead_uuid, user=2)[1].status == 404


def test_assignment_is_conflict_checked_and_emails_only_after_a_real_change(api, monkeypatch):
    call, factory, _ = api
    sent = []

    async def fake_send(**notification):
        # Sent only after the commit: a separate session already sees the owner.
        with factory() as other:
            notification['committed_owner'] = other.get(Client, 1).assigned_to
        sent.append(notification)

    monkeypatch.setattr(mobile_records_routes, 'send_assignment_notification', fake_send)
    with factory() as db:
        client_uuid = str(db.get(Client, 1).uuid)
        assert db.get(Client, 1).assigned_to is None

    result, key = assign(api, 'clients', client_uuid, 3, None)
    assert set(response(result, 200)) == {'id', 'uuid', 'record'}
    with factory() as db:
        assert db.get(Client, 1).assigned_to == 3
    assert [(n['to_email'], n['committed_owner']) for n in sent] == [('ordinary@example.test', 3)]

    # A replay of the same request sends nothing more.
    replay = call('POST', f'/api/mobile/records/clients/{client_uuid}/assign', {'assigned_to': 3, 'base': None}, key=key)
    assert response(replay, 200) == {'id': 1, 'uuid': client_uuid}
    # Already assigned to that person: a no-op, no email.
    assert assign(api, 'clients', client_uuid, 3, 3)[0].status == 200
    assert len(sent) == 1

    # Someone reassigned it since the phone's copy: a conflict, nothing changes.
    with factory() as db:
        db.get(Client, 1).assigned_to = 1
        db.commit()
    result, key = assign(api, 'clients', client_uuid, 3, None)
    conflict = response(result, 409)
    assert conflict['code'] == 'edit_conflict'
    assert conflict['conflicts'] == [{'field': 'assigned_to', 'base': None, 'mine': 3, 'theirs': 1}]
    with factory() as db:
        assert db.get(Client, 1).assigned_to == 1
        assert db.query(WriteReceipt).filter_by(request_key=key).count() == 0
    assert len(sent) == 1


def test_assignment_rules(api, monkeypatch):
    _, factory, _ = api
    sent = []

    async def fake_send(**notification):
        sent.append(notification)

    monkeypatch.setattr(mobile_records_routes, 'send_assignment_notification', fake_send)
    with factory() as db:
        client_uuid = str(db.get(Client, 1).uuid)
        lead_uuid = str(db.get(Lead, 1).uuid)
        project_uuid = str(db.get(Project, 1).uuid)
    assert assign(api, 'clients', client_uuid, 1, None, user=3)[0].status in (403, 404)  # not an admin
    assert assign(api, 'clients', client_uuid, 2, None)[0].status == 400  # another tenant's user
    with factory() as db:
        db.get(User, 3).is_active = False
        db.commit()
    assert assign(api, 'leads', lead_uuid, 3, None)[0].status == 400  # inactive user
    with factory() as db:
        db.get(User, 3).is_active = True
        db.commit()
    assert assign(api, 'projects', project_uuid, 3, None)[0].status == 200
    assert sent and sent[-1]['entity_type'] == 'project' and sent[-1]['to_email'] == 'ordinary@example.test'
    assert assign(api, 'contacts', client_uuid, 3, None)[0].status == 404  # not assignable
    call, _, _ = api
    path = f'/api/mobile/records/clients/{client_uuid}/assign'
    assert call('POST', path, {'assigned_to': 3}, key=new_key()).status == 400
    assert call('POST', path, {'assigned_to': True, 'base': None}, key=new_key()).status == 400
    assert call('POST', path, {'assigned_to': 3, 'base': 'x'}, key=new_key()).status == 400
    assert call('POST', path, {'assigned_to': 3, 'base': None}, key=new_key(), device=False).status == 403


def test_conversion_reports_won_even_from_a_won_lead_without_a_date(api):
    _, factory, _ = api
    lead_id, lead_uuid = make_lead(api, name='Already won')
    with factory() as db:
        lead = db.get(Lead, lead_id)
        lead.lead_status = 'Won'  # a company option spelled differently, no converted_on
        lead.converted_on = None
        db.commit()
    assert convert(api, lead_uuid)[1].status == 201
    with factory() as db:
        lead = db.get(Lead, lead_id)
        assert lead.lead_status == 'won' and lead.converted_on is not None


def test_a_child_create_waits_for_a_conversion_in_progress_and_then_is_refused(api):
    _, factory, _ = api
    _, lead_uuid = make_lead(api, name='Locked lead')
    outcome = {}
    with factory() as holder:
        # Stand in for a conversion holding the lead (FOR UPDATE) until it commits.
        lead = holder.query(Lead).filter(Lead.uuid == lead_uuid).with_for_update().one()

        def child():
            outcome['status'] = create(api, 'interactions', fields={
                'contact_date': '2026-11-25T11:00:00', 'summary': 'Raced'}, parents={'lead': {'uuid': lead_uuid}})[1].status

        worker = threading.Thread(target=child)
        worker.start()
        time.sleep(1.5)
        assert worker.is_alive(), 'the child create should wait for the lead lock'
        lead.deleted_at = datetime.utcnow()
        holder.commit()
    worker.join(timeout=30)
    assert outcome.get('status') == 404


def test_assignees_are_for_admins_only(api):
    call, _, _ = api
    roster = response(call('GET', '/api/mobile/assignees'), 200)['assignees']
    assert [person['email'] for person in roster] == ['a@example.test', 'ordinary@example.test']
    assert call('GET', '/api/mobile/assignees', user=3).status == 403
    assert call('GET', '/api/mobile/assignees', device=False).status == 403

