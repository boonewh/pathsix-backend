"""Phase 5: reviewed updates, notes, statuses and reassignment with stale protection."""
from datetime import datetime
from uuid import uuid4
import pytest
from app.models import AIClient, AIToolAudit, AIWriteAction, Client, Interaction, Lead, Project, User
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
    approve(updater, raw, 'project', propose(updater, raw, 'project', 1, {'project_status': 'won'}))
    approve(updater, raw, 'client', propose(updater, raw, 'client', 1, {'status': 'active'}))
    with updater.admin() as db:
        assert db.get(Project, 1).project_status == 'won' and db.get(Client, 1).status == 'active'


def test_updating_needs_the_update_permission_and_kind_isolation(updater):
    read_only = propose(updater, token(updater, scopes=['leads:read']), 'lead', 1, {'city': 'X'})
    assert read_only['isError'] and 'leads:update' in read_only['_meta']['mcp/www_authenticate'][0]
    raw = token(updater)
    proposed = propose(updater, raw, 'lead', 1, {'city': 'X'})
    assert call(updater, raw, 'get_contact_update', {'action_id': proposed['structuredContent']['action_id']})['isError']
