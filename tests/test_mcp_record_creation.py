"""Phase 4: reviewed creation of contacts, projects and interactions."""
from datetime import datetime
from uuid import uuid4
import pytest
from app.models import AIClient, AIToolAudit, AIWriteAction, Client, Contact, Interaction, Project
from app.services.ai_action_kinds import KINDS
from test_security_boundaries import crm
from test_mcp_reads import oauth, token_for, call, rpc

CREATE = ['contacts:read', 'contacts:create', 'projects:read', 'projects:create', 'interactions:read',
          'interactions:create', 'leads:read', 'leads:create', 'clients:read']


@pytest.fixture
def writer(oauth):
    with oauth.admin() as db:
        db.get(AIClient, 'pilot').allowed_scopes = CREATE
        db.commit()
    return oauth


def token(flow, user=1, scopes=CREATE):
    return token_for(flow, user, scopes)[0]


def prepare(flow, raw, noun, fields, key=None):
    return call(flow, raw, f'prepare_{noun}_creation', {'request_key': key or uuid4().hex, noun: fields})


def card_decide(flow, raw, noun, result, approved=True):
    args = {'action_id': result['structuredContent']['action_id'],
            'intent': result['_meta']['pathsix/review']['intent'], 'approved': approved}
    return call(flow, raw, f'decide_{noun}_creation', args)


def test_contact_is_added_once_after_card_approval(writer):
    raw = token(writer)
    key = uuid4().hex
    proposed = prepare(writer, raw, 'contact', {'first_name': 'Adam', 'last_name': 'Smith', 'client_id': 1,
                                                'phone': '(512) 555-0100'}, key)
    assert proposed['structuredContent']['status'] == 'pending'
    shown = proposed['_meta']['pathsix/presentation']
    assert shown['title'] == 'Add this contact?' and shown['confirm'] == 'Add contact'
    assert ['For', 'Client: Private client 1'] in shown['fields'] and ['First name', 'Adam'] in shown['fields']
    # Same key and input is a retry; a different input under that key is refused.
    again = prepare(writer, raw, 'contact', {'first_name': 'Adam', 'last_name': 'Smith', 'client_id': 1,
                                             'phone': '(512) 555-0100'}, key)
    assert again['structuredContent']['action_id'] == proposed['structuredContent']['action_id']
    assert prepare(writer, raw, 'contact', {'first_name': 'Eve', 'client_id': 1}, key)['isError']
    with writer.admin() as db:
        assert db.query(Contact).count() == 1
    saved = card_decide(writer, raw, 'contact', proposed)
    receipt = saved['structuredContent']
    assert receipt['status'] == 'committed' and receipt['contact']['name'] == 'Adam Smith'
    assert card_decide(writer, raw, 'contact', proposed)['structuredContent'] == receipt
    with writer.admin() as db:
        contact = db.get(Contact, receipt['contact']['id'])
        assert (contact.client_id, contact.lead_id, contact.tenant_id) == (1, None, 1)
        assert db.query(Contact).count() == 2
        assert db.query(AIToolAudit).filter_by(tool='confirm_contact_creation').count() == 1


@pytest.mark.parametrize('fields', [{'first_name': 'A'}, {'first_name': 'A', 'client_id': 1, 'lead_id': 1},
                                    {'first_name': 'A', 'client_id': 2}, {'first_name': 'A', 'client_id': 1, 'x': 1}])
def test_contact_parent_rules(writer, fields):
    assert prepare(writer, token(writer), 'contact', fields)['isError']
    with writer.admin() as db:
        assert db.query(AIWriteAction).count() == 0


def test_ordinary_user_cannot_attach_to_an_unassigned_client(writer):
    assert prepare(writer, token(writer, user=3), 'contact', {'first_name': 'A', 'client_id': 1})['isError']


def test_project_may_stand_alone_or_belong_to_a_lead(writer):
    raw = token(writer)
    alone = prepare(writer, raw, 'project', {'project_name': 'Standalone job'})
    saved = card_decide(writer, raw, 'project', alone)['structuredContent']
    assert saved['status'] == 'committed' and saved['project']['name'] == 'Standalone job'
    on_lead = prepare(writer, raw, 'project', {'project_name': 'Lead job', 'lead_id': 1, 'project_worth': 1200})
    assert ['For', 'Lead: Private lead 1'] in on_lead['_meta']['pathsix/presentation']['fields']
    card_decide(writer, raw, 'project', on_lead)
    with writer.admin() as db:
        assert db.query(Project).filter_by(project_name='Lead job', lead_id=1).one().project_worth == 1200


def test_interaction_keeps_local_times_and_appears_on_the_schedule(writer):
    raw = token(writer)
    proposed = prepare(writer, raw, 'interaction', {'client_id': 1, 'summary': 'Call Adam',
                                                    'contact_date': '2026-11-10T10:00', 'follow_up': '2026-11-17T09:30'})
    saved = card_decide(writer, raw, 'interaction', proposed)['structuredContent']
    with writer.admin() as db:
        row = db.get(Interaction, saved['interaction']['id'])
        assert (row.contact_date, row.follow_up) == (datetime(2026, 11, 10, 10, 0), datetime(2026, 11, 17, 9, 30))
        assert row.followup_status.value == 'pending'
    week = call(writer, raw, 'get_schedule', {'start_date': '2026-11-17', 'end_date': '2026-11-17'})['structuredContent']
    assert [i['summary'] for i in week['follow_ups']] == ['Call Adam']


@pytest.mark.parametrize('fields', [
    {'client_id': 1, 'summary': 'x', 'contact_date': '2026-11-10T10:00:00Z'},
    {'client_id': 1, 'summary': 'x', 'contact_date': '2026-11-10T10:00:00-06:00'},
    {'client_id': 1, 'summary': 'x', 'contact_date': '2026-11-10T10:00', 'followup_status': 'completed'},
    {'summary': 'x', 'contact_date': '2026-11-10T10:00'},
    {'client_id': 1, 'project_id': 1, 'summary': 'x', 'contact_date': '2026-11-10T10:00'}])
def test_interaction_rejects_time_zones_status_and_bad_parents(writer, fields):
    assert prepare(writer, token(writer), 'interaction', fields)['isError']


def test_time_zone_refusal_tells_the_ai_how_to_fix_it(writer):
    result = prepare(writer, token(writer), 'interaction',
                     {'client_id': 1, 'summary': 'x', 'contact_date': '2026-11-10T10:00:00Z'})
    assert result['isError'] and 'local times without a time zone' in result['content'][0]['text']


def test_each_kinds_tools_only_handle_their_own_proposals(writer):
    raw = token(writer)
    contact = prepare(writer, raw, 'contact', {'first_name': 'Adam', 'client_id': 1})
    ident = contact['structuredContent']['action_id']
    assert call(writer, raw, 'get_project_creation', {'action_id': ident})['isError']
    wrong = {'action_id': ident, 'intent': contact['_meta']['pathsix/review']['intent'], 'approved': True}
    assert call(writer, raw, 'decide_project_creation', wrong)['isError']
    with writer.admin() as db:
        assert db.get(AIWriteAction, ident).status == 'pending'


def test_creating_needs_the_create_permission(writer):
    result = prepare(writer, token(writer, scopes=['contacts:read']), 'contact', {'first_name': 'A', 'client_id': 1})
    assert result['isError'] and 'contacts:create' in result['_meta']['mcp/www_authenticate'][0]


def test_parent_change_after_review_requires_a_new_proposal(writer):
    raw = token(writer)
    proposed = prepare(writer, raw, 'contact', {'first_name': 'Adam', 'client_id': 1})
    with writer.admin() as db:
        db.get(Client, 1).name = 'Renamed client'
        db.commit()
    assert card_decide(writer, raw, 'contact', proposed)['isError']
    with writer.admin() as db:
        assert db.query(Contact).count() == 1


def test_untrusted_clients_see_no_decide_tools(writer):
    with writer.admin() as db:
        db.get(AIClient, 'pilot').inline_review = False
        db.commit()
    status, listing, _ = rpc(writer, token(writer))
    names = {tool['name'] for tool in listing['result']['tools']}
    assert not names & {kind.decide_tool for kind in KINDS.values()}
    assert {kind.prepare_tool for kind in KINDS.values()} <= names
