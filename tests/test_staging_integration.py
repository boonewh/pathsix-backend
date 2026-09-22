"""Combined production behavior through staging's tenant-bound services."""
import json
import pytest
from test_security_boundaries import crm
from app.models import ActivityLog, Lead, Tenant
from app.schemas.leads import LeadCreateSchema, LeadUpdateSchema
from app.services.leads import LeadService
from app.services.principal import Principal
from app.utils import auth_utils


@pytest.mark.parametrize('status', [None, '', '  '])
def test_service_defaults_and_transactional_history(crm, status):
    _, admin_factory, _ = crm
    with admin_factory() as db:
        db.get(Tenant, 1).config = {'leads': {'statuses': ['prospecting', 'won']}}
        db.commit()
    with auth_utils.SessionLocal() as db:
        service = LeadService(db, Principal(1, 1, frozenset({'admin'})))
        lead_id = service.create(LeadCreateSchema(name='Direct service', lead_status=status))
        assert service.detail(lead_id)['lead_status'] == 'prospecting'
        assert db.query(ActivityLog).filter_by(entity_type='lead', entity_id=lead_id).count() == 1
        db.commit()
        service.update(lead_id, LeadUpdateSchema(notes='Rollback'))
        db.rollback()
        assert db.query(ActivityLog).filter_by(entity_type='lead', entity_id=lead_id).count() == 1
        service.bulk_delete([lead_id])
        db.commit()
        events = db.query(ActivityLog).filter_by(entity_type='lead', entity_id=lead_id).all()
        assert sorted(e.action.value for e in events) == ['created', 'deleted']
        assert all((e.user_id, e.tenant_id) == (1, 1) for e in events)
        service.bulk_purge([lead_id])
        db.rollback()
        assert db.get(Lead, lead_id) is not None
        assert db.query(ActivityLog).filter_by(entity_type='lead', entity_id=lead_id).count() == 2


def test_report_contract_and_tenant_isolation_over_http(crm):
    call, _, _ = crm
    assert call('POST', '/api/leads', {'name': 'Tenant one work'})[0] == 201
    assert call('POST', '/api/leads', {'name': 'Tenant two private work'}, user=2)[0] == 201
    status, raw = call('GET', '/api/reports/sales-activity?page=1&user_id=1')
    report = json.loads(raw)
    assert status == 200 and report['total'] >= 1
    assert report['page'] == 1 and report['per_page'] == 50
    assert len(report['events']) == report['total']
    assert json.loads(call('GET', '/api/reports/sales-activity?page=2&user_id=1')[1])['events'] == []
    assert 'Tenant two private work' not in raw
    assert call('GET', '/api/reports/sales-activity', user=3)[0] == 403
