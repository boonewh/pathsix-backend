"""Phase 5: reviewed updates, notes, statuses and reassignment with stale protection."""
from datetime import datetime
from uuid import uuid4
import pytest
from app.models import AIClient, AIToolAudit, AIWriteAction, Client, Interaction, Lead, Project, Tenant, User
from app.services.ai_connections import PUBLISHED_SCOPES
from app.utils import email_utils
from test_security_boundaries import crm
from test_mcp_reads import oauth, token_for, call

ALL = sorted(PUBLISHED_SCOPES)


@pytest.fixture
def updater(oauth):
    with oauth.admin() as db:
        db.get(AIClient, 'pilot').allowed_scopes = ALL
        db.commit()
    return oauth


def token(flow, user=1, scopes=ALL):
    return token_for(flow, user, scopes)[0]


def propose(flow, raw, noun, ident, changes, **extra):
    return call(flow, raw, f'prepare_{noun}_update',
                {'request_key': uuid4().hex, f'{noun}_update': {f'{noun}_id': ident, 'changes': changes, **extra}})


def approve(flow, raw, noun, result):
    args = {'action_id': result['structuredContent']['action_id'],
            'intent': result['_meta']['pathsix/review']['intent'], 'approved': True}
    return call(flow, raw, f'decide_{noun}_update', args)


def rows(result):
    return result['_meta']['pathsix/presentation']['fields']


def test_lead_update_shows_before_and_after_and_saves_once(updater):
    raw = token(updater)
    proposed = propose(updater, raw, 'lead', 1, {'city': 'Austin', 'phone': '(512) 555-0100'})
    shown = proposed['_meta']['pathsix/presentation']
    assert shown['title'] == 'Update this lead?' and shown['confirm'] == 'Save changes'
    assert ['Record', 'Private lead 1'] in rows(proposed) and ['City', '(empty) → Austin'] in rows(proposed)
    assert ['Phone', '(empty) → +15125550100'] in rows(proposed)
    saved = approve(updater, raw, 'lead', proposed)['structuredContent']
    assert saved['status'] == 'committed' and saved['lead']['name'] == 'Private lead 1'
    assert approve(updater, raw, 'lead', proposed)['structuredContent'] == saved
    with updater.admin() as db:
        lead = db.get(Lead, 1)
        assert (lead.city, lead.phone) == ('Austin', '+15125550100')
        assert db.query(AIToolAudit).filter_by(tool='confirm_lead_update').count() == 1


def test_notes_are_appended_unless_replacement_is_requested(updater):
    raw = token(updater)
    with updater.admin() as db:
        db.get(Lead, 1).notes = 'Met at expo'
        db.commit()
    added = propose(updater, raw, 'lead', 1, {'notes': 'Wants a quote'})
    assert ['Notes (adding)', 'Wants a quote'] in rows(added)
    approve(updater, raw, 'lead', added)
    with updater.admin() as db:
        assert db.get(Lead, 1).notes == 'Met at expo\n\nWants a quote'
    replaced = propose(updater, raw, 'lead', 1, {'notes': 'Fresh start'}, notes_mode='replace')
    assert ['Notes (replacing)', 'Met at expo\n\nWants a quote → Fresh start'] in rows(replaced)
    approve(updater, raw, 'lead', replaced)
    with updater.admin() as db:
        assert db.get(Lead, 1).notes == 'Fresh start'


def test_a_change_made_after_review_blocks_the_stale_save(updater):
    raw = token(updater)
    proposed = propose(updater, raw, 'lead', 1, {'city': 'Austin'})
    with updater.admin() as db:
        db.get(Lead, 1).city = 'Dallas'  # Someone edits the same field in the CRM meanwhile.
        db.commit()
    assert approve(updater, raw, 'lead', proposed)['isError']
    with updater.admin() as db:
        assert db.get(Lead, 1).city == 'Dallas'
    # An edit to a different field does not block an unrelated change.
    other = propose(updater, raw, 'lead', 1, {'state': 'TX'})
    with updater.admin() as db:
        db.get(Lead, 1).zip = '78701'
        db.commit()
    assert approve(updater, raw, 'lead', other)['structuredContent']['status'] == 'committed'


def test_no_op_and_unsupported_changes_are_refused(updater):
    raw = token(updater)
    assert propose(updater, raw, 'lead', 1, {'name': 'Private lead 1'})['isError']
    assert propose(updater, raw, 'contact', 1, {'client_id': 2})['isError']   # No parent moves.
    assert propose(updater, raw, 'lead', 1, {})['isError']
    assert propose(updater, raw, 'lead', 2, {'city': 'X'})['isError']          # Another tenant.
    assert propose(updater, token(updater, user=3), 'client', 1, {'status': 'active'})['isError']
    with updater.admin() as db:
        assert db.query(AIWriteAction).count() == 0


def test_admin_reassignment_names_people_and_notifies_after_save(updater, monkeypatch):
    sent = []
    async def fake_send(**notification):
        sent.append(notification)
    monkeypatch.setattr(email_utils, 'send_assignment_notification', fake_send)
    raw = token(updater)
    listing = call(updater, raw, 'list_assignees')['structuredContent']['assignees']
    assert [person['id'] for person in listing] == [1, 3]
    proposed = propose(updater, raw, 'lead', 1, {'assigned_to': 3})
    assert ['Assigned to', 'Unassigned → ordinary@example.test'] in rows(proposed)
    approve(updater, raw, 'lead', proposed)
    updater.runner.run(__import__('asyncio').sleep(0))
    with updater.admin() as db:
        assert db.get(Lead, 1).assigned_to == 3
    assert sent == [{'to_email': 'ordinary@example.test', 'entity_type': 'lead',
                     'entity_name': 'Private lead 1', 'assigned_by': 'a@example.test'}]


@pytest.mark.parametrize('assignee', [2, 999])
def test_assignee_must_be_active_in_the_same_company(updater, assignee):
    assert propose(updater, token(updater), 'lead', 1, {'assigned_to': assignee})['isError']


def test_only_administrators_reassign(updater):
    with updater.admin() as db:
        db.get(Lead, 1).assigned_to = 3
        db.commit()
    ordinary = token(updater, user=3)
    assert call(updater, ordinary, 'list_assignees')['isError']
    assert propose(updater, ordinary, 'lead', 1, {'assigned_to': 1})['isError']
    assert propose(updater, ordinary, 'lead', 1, {'city': 'Waco'})['structuredContent']['status'] == 'pending'


def test_completing_a_follow_up_removes_it_from_the_schedule(updater):
    raw = token(updater)
    week = {'start_date': '2026-09-10', 'end_date': '2026-09-10'}
    assert [i['id'] for i in call(updater, raw, 'get_schedule', week)['structuredContent']['follow_ups']] == [1]
    proposed = propose(updater, raw, 'interaction', 1, {'followup_status': 'completed', 'outcome': 'Done'})
    assert ['Followup status', 'pending → completed'] in rows(proposed)
    approve(updater, raw, 'interaction', proposed)
    assert call(updater, raw, 'get_schedule', week)['structuredContent']['follow_ups'] == []


def test_project_status_and_client_status_update(updater):
    raw = token(updater)
    approve(updater, raw, 'project', propose(updater, raw, 'project', 1, {'project_status': 'completed'}))
    approve(updater, raw, 'client', propose(updater, raw, 'client', 1, {'status': 'active'}))
    with updater.admin() as db:
        assert db.get(Project, 1).project_status == 'completed' and db.get(Client, 1).status == 'active'


def test_updating_needs_the_update_permission_and_kind_isolation(updater):
    read_only = propose(updater, token(updater, scopes=['leads:read']), 'lead', 1, {'city': 'X'})
    assert read_only['isError'] and 'leads:update' in read_only['_meta']['mcp/www_authenticate'][0]
    raw = token(updater)
    proposed = propose(updater, raw, 'lead', 1, {'city': 'X'})
    assert call(updater, raw, 'get_contact_update', {'action_id': proposed['structuredContent']['action_id']})['isError']


# Batch 1 of web parity (todo 95) ------------------------------------------------

def refused(result, words):
    assert result['isError'] and words in str(result), result


def test_fields_the_web_form_always_fills_cannot_be_cleared(updater):
    raw = token(updater)
    refused(propose(updater, raw, 'project', 1, {'project_status': None}), 'cannot be cleared')
    refused(propose(updater, raw, 'project', 1, {'project_status': '  '}), 'cannot be cleared')
    refused(propose(updater, raw, 'project', 1, {'value_type': None}), 'cannot be cleared')
    refused(propose(updater, raw, 'project', 1, {'type': ''}), 'cannot be cleared')
    refused(propose(updater, raw, 'interaction', 1, {'followup_status': None}), 'cannot be cleared')
    refused(propose(updater, raw, 'lead', 1, {'lead_status': ''}), 'cannot be cleared')
    refused(propose(updater, raw, 'client', 1, {'status': None}), 'cannot be cleared')
    refused(propose(updater, raw, 'contact', 1, {'first_name': '   '}), 'cannot be cleared')
    with updater.admin() as db:
        assert db.query(AIWriteAction).count() == 0


def test_listed_fields_take_the_web_forms_values(updater):
    raw = token(updater)
    refused(propose(updater, raw, 'project', 1, {'value_type': 'weekly'}), 'must be one of')
    refused(propose(updater, raw, 'project', 1, {'project_status': 'won'}), 'must be one of')  # Not offered by the web form.
    refused(propose(updater, raw, 'client', 1, {'status': 'dormant'}), 'must be one of')
    refused(propose(updater, raw, 'client', 1, {'type': 'Aerospace'}), 'must be one of')  # Default business types.
    with updater.admin() as db:
        db.get(Tenant, 1).config = {'businessTypes': ['Aerospace'], 'leads': {'statuses': ['New inquiry', 'Qualified']}}
        db.get(Project, 1).type = 'Legacy kind'
        db.commit()
    assert propose(updater, raw, 'client', 1, {'type': 'aerospace'})['structuredContent']['status'] == 'pending'
    refused(propose(updater, raw, 'lead', 1, {'lead_status': 'Lost interest'}), 'must be one of')
    assert propose(updater, raw, 'lead', 1, {'lead_status': 'Qualified'})['structuredContent']['status'] == 'pending'
    # The record's current value stays allowed, as the web form keeps it selectable.
    kept = propose(updater, raw, 'project', 1, {'type': 'Legacy kind', 'value_type': 'monthly'})
    assert kept['structuredContent']['status'] == 'pending'


def legacy_proposal(flow, user, payload):
    """A pending update stored by an earlier release (proposals are immutable, so insert one)."""
    from datetime import timedelta
    raw, connection = token_for(flow, user, ALL)
    ident = str(uuid4())
    now = datetime.utcnow()
    with flow.admin() as db:
        db.add(AIWriteAction(id=ident, tenant_id=1, user_id=user, connection_id=connection,
                             request_key=uuid4().hex, input_hash='legacy', kind='update_project',
                             payload=payload, status='pending', created_at=now, expires_at=now + timedelta(minutes=10)))
        db.commit()
    return raw, call(flow, raw, 'get_project_update', {'action_id': ident})


def test_a_proposal_made_before_a_rule_is_checked_again_at_approval(updater):
    raw, status = legacy_proposal(updater, 1, {
        'project_id': 1, 'record_name': 'Private project 1', 'notes_mode': 'append',
        'changes': {'project_status': None}, 'before': {'project_status': 'pending'}, 'display': {}})
    refused(approve(updater, raw, 'project', status), 'cannot be cleared')
    with updater.admin() as db:
        assert db.get(Project, 1).project_status == 'pending'


def test_approval_refuses_a_proposal_whose_save_would_differ_from_its_review(updater):
    with updater.admin() as db:
        db.get(Project, 1).project_worth = 500
        db.commit()
    # Reviewed as "500.0 -> (empty)" by an earlier release, but the service would save 0.
    raw, status = legacy_proposal(updater, 1, {
        'project_id': 1, 'record_name': 'Private project 1', 'notes_mode': 'append',
        'changes': {'project_worth': None}, 'before': {'project_worth': 500.0}, 'display': {}})
    refused(approve(updater, raw, 'project', status), 'differs from what you reviewed')
    with updater.admin() as db:
        assert db.get(Project, 1).project_worth == 500


def test_listed_values_are_saved_with_the_web_forms_spelling(updater):
    with updater.admin() as db:
        db.get(Tenant, 1).config = {'businessTypes': ['Aerospace']}
        db.commit()
    raw = token(updater)
    proposed = propose(updater, raw, 'project', 1, {'project_status': 'COMPLETED', 'value_type': ' Monthly '})
    assert ['Project status', 'pending → completed'] in rows(proposed)
    approve(updater, raw, 'project', proposed)
    approve(updater, raw, 'client', propose(updater, raw, 'client', 1, {'type': 'aerospace'}))
    with updater.admin() as db:
        project = db.get(Project, 1)
        assert (project.project_status, project.value_type) == ('completed', 'monthly')
        assert db.get(Client, 1).type == 'Aerospace'


def test_an_exact_web_choice_corrects_a_legacy_spelling(updater):
    with updater.admin() as db:
        db.get(Project, 1).project_status = 'COMPLETED'
        db.commit()
    raw = token(updater)
    # The exact listed spelling wins over the record's case-equivalent legacy value.
    proposed = propose(updater, raw, 'project', 1, {'project_status': 'completed'})
    assert ['Project status', 'COMPLETED → completed'] in rows(proposed)
    # Keeping the exact legacy value is a no-op, as on the web.
    refused(propose(updater, raw, 'project', 1, {'project_status': 'COMPLETED'}), 'Nothing would change')


def test_storage_limits_apply_to_the_saved_spelling(updater):
    # 'ß' case-folds to 'ss': a 26-character input matches a 51-character configured choice,
    # longer than the 50-character type column. The saved spelling is what must fit.
    configured = 'SS' * 25 + 'X'
    with updater.admin() as db:
        db.get(Tenant, 1).config = {'businessTypes': [configured]}
        db.commit()
    raw = token(updater)
    assert propose(updater, raw, 'client', 1, {'type': 'ß' * 25 + 'x'})['isError']
    with updater.admin() as db:
        assert db.query(AIWriteAction).count() == 0


def test_a_cleared_project_value_is_reviewed_as_it_will_be_saved(updater):
    with updater.admin() as db:
        db.get(Project, 1).project_worth = 500
        db.commit()
    raw = token(updater)
    proposed = propose(updater, raw, 'project', 1, {'project_worth': None})
    assert ['Project worth', '500.0 → 0'] in rows(proposed)
    approve(updater, raw, 'project', proposed)
    with updater.admin() as db:
        assert db.get(Project, 1).project_worth == 0


def test_interaction_phone_is_saved_as_typed_like_the_web(updater):
    raw = token(updater)
    proposed = propose(updater, raw, 'interaction', 1, {'phone': '(512) 555-0100'})
    assert ['Phone', '(empty) → (512) 555-0100'] in rows(proposed)
    approve(updater, raw, 'interaction', proposed)
    with updater.admin() as db:
        assert db.get(Interaction, 1).phone == '(512) 555-0100'


def open_review(flow, ident, user):
    from test_mcp_lead_creation import preview
    flow.request('GET', '/oauth/actions/' + ident)  # Sets the browser binding, as the page does.
    return preview(flow, ident, user)


def test_a_pending_update_stops_showing_fields_once_access_is_lost(updater):
    with updater.admin() as db:
        db.get(Lead, 1).assigned_to = 3
        db.commit()
    ordinary = token(updater, user=3)
    proposed = propose(updater, ordinary, 'lead', 1, {'notes': 'Private follow-up detail'})
    ident = proposed['structuredContent']['action_id']
    assert 'pathsix/review' in proposed['_meta']
    status, body = open_review(updater, ident, 3)
    assert status == 200 and body['lead']['changes'] and body.get('intent')
    with updater.admin() as db:
        db.get(Lead, 1).assigned_to = 1  # Handed to someone else: user 3 can no longer open it.
        db.commit()
    refreshed = call(updater, ordinary, 'get_lead_update', {'action_id': ident})
    assert 'pathsix/review' not in refreshed['_meta']
    assert 'fields' not in refreshed['_meta']['pathsix/presentation']
    assert 'Private follow-up detail' not in str(refreshed)
    status, body = open_review(updater, ident, 3)
    assert status == 404 and 'no longer available' in body['error'] and 'Private follow-up detail' not in str(body)
    assert approve(updater, ordinary, 'lead', proposed)['isError']


def test_a_pending_creation_stops_showing_fields_once_its_parent_is_lost(updater):
    with updater.admin() as db:
        db.get(Client, 1).assigned_to = 3
        db.commit()
    ordinary = token(updater, user=3)
    proposed = call(updater, ordinary, 'prepare_contact_creation',
                    {'request_key': uuid4().hex, 'contact': {'client_id': 1, 'first_name': 'Secret', 'notes': 'Private note'}})
    ident = proposed['structuredContent']['action_id']
    assert 'pathsix/review' in proposed['_meta']
    with updater.admin() as db:
        db.get(Client, 1).assigned_to = 1
        db.commit()
    refreshed = call(updater, ordinary, 'get_contact_creation', {'action_id': ident})
    assert 'pathsix/review' not in refreshed['_meta'] and 'Private note' not in str(refreshed)
    status, body = open_review(updater, ident, 3)
    assert status == 404 and 'Private note' not in str(body)


def test_inline_assignment_email_is_tracked_until_sent(updater, monkeypatch):
    import asyncio
    import gc
    from app.services import mcp_reads
    release, sent = asyncio.Event(), []

    async def slow_send(**notification):
        await release.wait()
        sent.append(notification['to_email'])
    monkeypatch.setattr(email_utils, 'send_assignment_notification', slow_send)
    raw = token(updater)
    approve(updater, raw, 'client', propose(updater, raw, 'client', 1, {'assigned_to': 3}))
    gc.collect()
    assert len(mcp_reads._DELIVERIES) == 1 and sent == []  # Held by a strong reference while waiting.
    release.set()
    for _ in range(3):
        updater.runner.run(asyncio.sleep(0))
    assert sent == ['ordinary@example.test'] and not mcp_reads._DELIVERIES


def test_a_project_person_matching_an_existing_contact_saves_only_what_was_reviewed(updater):
    from app.models import Contact
    with updater.admin() as db:
        db.get(Project, 1).client_id = 1
        contact = db.get(Contact, 1)
        contact.email, contact.title = 'synthetic.person@example.com', 'Owner'
        db.commit()
    raw = token(updater)
    # Same email as the client's existing person, different title: the save would
    # keep "Owner", which is not what the review shows.
    proposed = propose(updater, raw, 'project', 1, {'primary_contact_email': 'synthetic.person@example.com',
                                                    'primary_contact_title': 'Buyer'})
    refused(approve(updater, raw, 'project', proposed), 'differs from what you reviewed')
    with updater.admin() as db:
        assert db.get(Project, 1).primary_contact_id is None
        assert db.get(Contact, 1).title == 'Owner'
    # Details that agree with the existing person are saved as reviewed.
    proposed = propose(updater, raw, 'project', 1, {'primary_contact_email': 'synthetic.person@example.com'})
    assert not approve(updater, raw, 'project', proposed)['isError']
    with updater.admin() as db:
        assert db.get(Project, 1).primary_contact_id == 1
