"""Leads list status filter and per-status counts (GET /api/leads/?status=)."""
import json
from datetime import datetime

from purge_fixture import crm
from app.models import Lead
from app.services.leads import LeadService
from app.services.principal import Principal


def seed(factory):
    """User 3 (tenant 1) can see leads 10-13; 14-16 must never be visible or counted."""
    with factory() as db:
        db.add_all([
            Lead(id=10, tenant_id=1, created_by=3, name='Open A', lead_status='open'),
            Lead(id=11, tenant_id=1, created_by=3, name='Open B', lead_status='open'),
            Lead(id=12, tenant_id=1, created_by=1, assigned_to=3, name='Qualified', lead_status='qualified'),
            Lead(id=13, tenant_id=1, created_by=3, name='Won', lead_status='won'),
            # Same tenant, assigned to someone else.
            Lead(id=14, tenant_id=1, created_by=3, assigned_to=1, name='Not mine', lead_status='qualified'),
            # Deleted.
            Lead(id=15, tenant_id=1, created_by=3, name='Deleted', lead_status='open', deleted_at=datetime(2026, 10, 1)),
            # Other tenant.
            Lead(id=16, tenant_id=2, created_by=2, name='Other tenant', lead_status='qualified'),
        ])
        db.commit()


def test_service_filters_by_status_and_counts_visible_leads(crm):
    _, factory, _ = crm
    seed(factory)
    with factory() as db:
        service = LeadService(db, Principal(3, 1, frozenset()))
        everything = service.list_mine(per_page=50)
        assert everything['total'] == 4
        assert everything['status'] is None
        assert everything['status_counts'] == {'open': 2, 'qualified': 1, 'won': 1}

        qualified = service.list_mine(per_page=50, status='qualified')
        assert [lead['id'] for lead in qualified['leads']] == [12]
        assert qualified['total'] == 1
        assert qualified['status'] == 'qualified'
        # Counts ignore the filter so every status button keeps its number.
        assert qualified['status_counts'] == everything['status_counts']

        assert service.list_mine(per_page=50, status='lost')['total'] == 0
        assert service.list_mine(per_page=50, status='   ')['total'] == 4


def test_http_status_filter_pages_across_all_matching_leads(crm):
    call, factory, _ = crm
    seed(factory)
    status, body = call('GET', '/api/leads/?status=open&per_page=1&page=2', user=3)
    assert status == 200
    data = json.loads(body)
    assert data['total'] == 2
    assert len(data['leads']) == 1
    assert data['leads'][0]['lead_status'] == 'open'
    assert data['status_counts'] == {'open': 2, 'qualified': 1, 'won': 1}

    status, body = call('GET', '/api/leads/', user=3)
    assert status == 200
    data = json.loads(body)
    assert {lead['id'] for lead in data['leads']} == {10, 11, 12, 13}


def test_counts_never_include_other_tenants(crm):
    call, factory, _ = crm
    seed(factory)
    status, body = call('GET', '/api/leads/?status=qualified', user=2)
    assert status == 200
    data = json.loads(body)
    assert {lead['id'] for lead in data['leads']} == {16}
    # Tenant 2's user sees only lead 16 and the fixture's lead 2 (default "open");
    # tenant 1's leads never appear in the counts.
    assert data['status_counts'] == {'qualified': 1, 'open': 1}
