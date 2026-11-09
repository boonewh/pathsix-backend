"""Phase 3 delegated reads: contacts, projects, interactions, client detail, schedule."""
from datetime import datetime
import pytest
from sqlalchemy import text
from app.models import AIClient, Client, Contact, FollowUpStatus, Interaction, Project
from test_security_boundaries import crm
from test_mcp_reads import oauth, token_for, call

ALL = ['clients:read', 'contacts:read', 'projects:read', 'interactions:read']


@pytest.fixture
def reads(oauth):
    with oauth.admin() as db:
        db.get(AIClient, 'pilot').allowed_scopes = ALL
        db.commit()
    return oauth


def tool(flow, name, args=None, user=1, scopes=ALL):
    token, _ = token_for(flow, user, scopes)
    return call(flow, token, name, args or {})


def ok(result):
    assert not result.get('isError'), result
    return result['structuredContent']


def test_contacts_follow_parent_access(reads):
    found = ok(tool(reads, 'list_contacts', {'query': 'A'}))['contacts']
    assert [(c['id'], c['client_id'], c['client_name']) for c in found] == [(1, 1, 'Private client 1')]
    assert ok(tool(reads, 'get_contact', {'contact_id': 1}))['contact']['first_name'] == 'A'
    assert ok(tool(reads, 'list_contacts', {'client_id': 1}))['contacts'][0]['id'] == 1
    # Another tenant and an unassigned ordinary user see nothing.
    assert ok(tool(reads, 'list_contacts', {}, user=2))['contacts'] == []
    assert ok(tool(reads, 'list_contacts', {}, user=3))['contacts'] == []
    assert tool(reads, 'get_contact', {'contact_id': 1}, user=3)['isError']
    assert tool(reads, 'list_contacts', {'client_id': 1}, user=3)['isError']
    with reads.admin() as db:
        db.get(Client, 1).assigned_to = 3
        db.commit()
    assert ok(tool(reads, 'list_contacts', {}, user=3))['contacts'][0]['id'] == 1


def test_projects_hide_archived_other_tenants_and_inaccessible_parent_names(reads):
    projects = ok(tool(reads, 'list_projects', {}))['projects']
    assert [p['id'] for p in projects] == [1]
    assert ok(tool(reads, 'list_projects', {'status': 'pending'}))['projects'][0]['project_name'] == 'Private project 1'
    assert ok(tool(reads, 'list_projects', {'status': 'won'}))['projects'] == []
    assert tool(reads, 'get_project', {'project_id': 2})['isError']
    with reads.admin() as db:
        # A project assigned to an ordinary user under a client they cannot see.
        db.add(Project(id=9, tenant_id=1, created_by=1, assigned_to=3, client_id=1,
                       project_name='Assigned job', project_status='pending'))
        db.commit()
    mine = ok(tool(reads, 'get_project', {'project_id': 9}, user=3))['project']
    assert mine['project_name'] == 'Assigned job' and mine.get('client_name') is None
    with reads.admin() as db:
        # Archiving is guarded at the ORM layer; set it directly as the owner endpoint would.
        db.execute(text('UPDATE projects SET archived_at = :at WHERE id = 9'), {'at': datetime(2026, 1, 1)})
        db.commit()
    assert tool(reads, 'get_project', {'project_id': 9}, user=3)['isError']
    assert ok(tool(reads, 'list_projects', {}, user=3))['projects'] == []


def test_interactions_need_one_accessible_parent(reads):
    items = ok(tool(reads, 'list_interactions', {'client_id': 1}))['interactions']
    assert [(i['id'], i['client_name'], i['followup_status']) for i in items] == [(1, 'Private client 1', 'pending')]
    assert ok(tool(reads, 'list_interactions', {'client_id': 2}))['interactions'] == []
    assert tool(reads, 'list_interactions', {})['isError']
    assert tool(reads, 'list_interactions', {'client_id': 1, 'lead_id': 1})['isError']


def test_client_detail_requires_access(reads):
    assert ok(tool(reads, 'get_client', {'client_id': 1}))['client']['name'] == 'Private client 1'
    assert tool(reads, 'get_client', {'client_id': 1}, user=3)['isError']
    assert tool(reads, 'get_client', {'client_id': 2})['isError']


def schedule_rows(db):
    db.query(Interaction).delete()
    rows = [
        (10, datetime(2026, 11, 9, 9, 0), datetime(2026, 11, 2), FollowUpStatus.pending),       # due today 9:00
        (11, datetime(2026, 11, 9, 23, 30), datetime(2026, 11, 2), FollowUpStatus.pending),     # due late today
        (12, datetime(2026, 11, 10, 0, 0), datetime(2026, 11, 2), FollowUpStatus.pending),      # tomorrow
        (13, datetime(2026, 11, 9, 10, 0), datetime(2026, 11, 2), FollowUpStatus.completed),    # done: not due
        (14, datetime(2026, 11, 5, 9, 0), datetime(2026, 11, 1), FollowUpStatus.pending),       # overdue
        (15, None, datetime(2026, 11, 9, 14, 0), FollowUpStatus.pending),                      # contact today
    ]
    for ident, follow_up, contact_date, status in rows:
        db.add(Interaction(id=ident, tenant_id=1, client_id=1, summary=f'Item {ident}', contact_date=contact_date,
                           follow_up=follow_up, followup_status=status))
    db.add(Interaction(id=20, tenant_id=2, client_id=2, summary='Other tenant', contact_date=datetime(2026, 11, 9),
                       follow_up=datetime(2026, 11, 9, 9, 0)))
    db.commit()


def test_schedule_uses_the_users_local_days(reads):
    with reads.admin() as db:
        schedule_rows(db)
    today = ok(tool(reads, 'get_schedule', {'start_date': '2026-11-09', 'end_date': '2026-11-09'}))
    assert [i['id'] for i in today['follow_ups']] == [10, 11]
    assert [i['id'] for i in today['contacts']] == [15]
    assert [i['id'] for i in today['overdue_follow_ups']] == [14]
    assert today['follow_ups'][0]['follow_up'] == '2026-11-09T09:00:00' and today['truncated'] is False
    week = ok(tool(reads, 'get_schedule', {'start_date': '2026-11-09', 'end_date': '2026-11-15'}))
    assert [i['id'] for i in week['follow_ups']] == [10, 11, 12]
    # An ordinary unassigned user sees none of the admin's schedule.
    hidden = ok(tool(reads, 'get_schedule', {'start_date': '2026-11-09', 'end_date': '2026-11-09'}, user=3))
    assert hidden['follow_ups'] == hidden['contacts'] == hidden['overdue_follow_ups'] == []


@pytest.mark.parametrize('dates', [{'start_date': '2026-11-09', 'end_date': '2026-12-10'},
                                   {'start_date': '2026-11-10', 'end_date': '2026-11-09'},
                                   {'start_date': '11/09/2026', 'end_date': '2026-11-09'},
                                   {'start_date': '2026-11-09'}])
def test_schedule_rejects_bad_ranges(reads, dates):
    assert tool(reads, 'get_schedule', dates)['isError']


def test_new_reads_need_their_own_permission(reads):
    result = tool(reads, 'list_contacts', {}, scopes=['clients:read'])
    assert result['isError']
    challenge = result['_meta']['mcp/www_authenticate'][0]
    assert 'insufficient_scope' in challenge and 'contacts:read' in challenge and 'clients:read' in challenge
