"""Accounts type filter (GET /api/clients/?type=) and Projects status filter
(GET /api/projects/?status=), each with counts that ignore the filter."""
import json
from datetime import datetime

from purge_fixture import crm
from app.models import Client, Project
from app.services.clients import ClientService
from app.services.projects import ProjectService
from app.services.principal import Principal


def seed(factory):
    """User 3 (tenant 1) can see clients/projects 10-13; 14-16 must never be visible or counted."""
    with factory() as db:
        db.add_all([
            Client(id=10, tenant_id=1, created_by=3, name='Clinic A', type='Healthcare'),
            Client(id=11, tenant_id=1, created_by=3, name='Clinic B', type='Healthcare'),
            Client(id=12, tenant_id=1, created_by=1, assigned_to=3, name='Freight', type='Logistics'),
            # No type given: the model stores its default, the text 'None'.
            Client(id=13, tenant_id=1, created_by=3, name='No type'),
            Client(id=14, tenant_id=1, created_by=3, assigned_to=1, name='Not mine', type='Logistics'),
            Client(id=15, tenant_id=1, created_by=3, name='Deleted', type='Healthcare', deleted_at=datetime(2026, 10, 1)),
            Client(id=16, tenant_id=2, created_by=2, name='Other tenant', type='Logistics'),
        ])
        db.add_all([
            Project(id=10, tenant_id=1, created_by=3, project_name='Active A', project_status='active'),
            Project(id=11, tenant_id=1, created_by=3, project_name='Active B', project_status='active'),
            Project(id=12, tenant_id=1, created_by=1, assigned_to=3, project_name='Pending', project_status='pending'),
            Project(id=13, tenant_id=1, created_by=3, project_name='Completed', project_status='completed'),
            Project(id=14, tenant_id=1, created_by=3, assigned_to=1, project_name='Not mine', project_status='pending'),
            Project(id=15, tenant_id=1, created_by=3, project_name='Deleted', project_status='active', deleted_at=datetime(2026, 10, 1)),
            Project(id=16, tenant_id=2, created_by=2, project_name='Other tenant', project_status='pending'),
        ])
        db.commit()


def test_client_type_filter_and_counts(crm):
    _, factory, _ = crm
    seed(factory)
    with factory() as db:
        service = ClientService(db, Principal(3, 1, frozenset()))
        everything = service.list_mine(per_page=50)
        assert everything['total'] == 4
        assert everything['type'] is None
        assert everything['type_counts'] == {'Healthcare': 2, 'Logistics': 1, 'None': 1}

        healthcare = service.list_mine(per_page=50, client_type='Healthcare')
        assert sorted(c['id'] for c in healthcare['clients']) == [10, 11]
        assert healthcare['total'] == 2
        assert healthcare['type'] == 'Healthcare'
        # Counts ignore the filter so every option keeps its number.
        assert healthcare['type_counts'] == everything['type_counts']

        assert service.list_mine(per_page=50, client_type='Retail')['total'] == 0
        assert service.list_mine(per_page=50, client_type='  ')['total'] == 4


def test_client_type_filter_pages_across_all_matches_over_http(crm):
    call, factory, _ = crm
    seed(factory)
    status, body = call('GET', '/api/clients/?type=Healthcare&per_page=1&page=2', user=3)
    assert status == 200
    data = json.loads(body)
    assert data['total'] == 2
    assert len(data['clients']) == 1
    assert data['clients'][0]['type'] == 'Healthcare'
    assert data['type_counts'] == {'Healthcare': 2, 'Logistics': 1, 'None': 1}

    status, body = call('GET', '/api/clients/', user=3)
    assert status == 200
    assert {c['id'] for c in json.loads(body)['clients']} == {10, 11, 12, 13}


def test_project_status_filter_and_counts(crm):
    _, factory, _ = crm
    seed(factory)
    with factory() as db:
        service = ProjectService(db, Principal(3, 1, frozenset()))
        everything = service.list_mine(per_page=50)
        assert everything['total'] == 4
        assert everything['status'] is None
        assert everything['status_counts'] == {'active': 2, 'pending': 1, 'completed': 1}

        active = service.list_mine(per_page=50, status='active')
        assert sorted(p['id'] for p in active['projects']) == [10, 11]
        assert active['total'] == 2
        assert active['status'] == 'active'
        assert active['status_counts'] == everything['status_counts']

        assert service.list_mine(per_page=50, status='lost')['total'] == 0
        assert service.list_mine(per_page=50, status='')['total'] == 4


def test_project_status_filter_pages_across_all_matches_over_http(crm):
    call, factory, _ = crm
    seed(factory)
    status, body = call('GET', '/api/projects/?status=active&per_page=1&page=2', user=3)
    assert status == 200
    data = json.loads(body)
    assert data['total'] == 2
    assert len(data['projects']) == 1
    assert data['projects'][0]['project_status'] == 'active'
    assert data['status_counts'] == {'active': 2, 'pending': 1, 'completed': 1}


def test_counts_never_include_other_tenants(crm):
    call, factory, _ = crm
    seed(factory)
    status, body = call('GET', '/api/clients/?type=Logistics', user=2)
    assert status == 200
    data = json.loads(body)
    assert {c['id'] for c in data['clients']} == {16}
    # Tenant 2 sees client 16 plus the fixture's client 2 (default type 'None').
    assert data['type_counts'] == {'Logistics': 1, 'None': 1}

    status, body = call('GET', '/api/projects/?status=pending', user=2)
    assert status == 200
    data = json.loads(body)
    # Tenant 2 sees project 16 and the fixture's project 2, both pending.
    assert {p['id'] for p in data['projects']} == {2, 16}
    assert data['status_counts'] == {'pending': 2}
