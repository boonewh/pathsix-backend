"""Report reads must retain tenant isolation when invoked without an HTTP request."""
import json
from datetime import datetime, timedelta

import pytest

from test_security_boundaries import crm
from app.models import ActivityLog, Client, Interaction, Lead, Project, Subscription, User
from app.services.principal import Principal
from app.services.reports import ReportService
from app.utils import auth_utils


REPORTS = {
    "get_reports": "",
    "sales_activity_report": "/sales-activity",
    "sales_pipeline": "/pipeline",
    "lead_source_report": "/lead-source",
    "conversion_rate_report": "/conversion-rate",
    "revenue_by_client": "/revenue-by-client",
    "user_activity_report": "/user-activity",
    "follow_up_report": "/follow-ups",
    "client_retention_report": "/client-retention",
    "project_performance_report": "/project-performance",
    "upcoming_tasks_report": "/upcoming-tasks",
    "revenue_forecast_report": "/revenue-forecast",
    "subscription_income_report": "/subscriptions/income",
    "upcoming_renewals_report": "/subscriptions/upcoming-renewals",
    "converted_leads_report": "/converted-leads",
}


@pytest.fixture
def report_data(crm):
    _, factory, _ = crm
    now = datetime.utcnow()
    with factory() as db:
        for tenant in (1, 2):
            client = db.get(Client, tenant)
            client.assigned_to = tenant
            client.source_lead_id = tenant
            client.created_at = now - timedelta(days=10)
            lead = db.get(Lead, tenant)
            lead.assigned_to = tenant
            lead.lead_status = "won"
            lead.lead_source = "own-source" if tenant == 1 else "FOREIGN-SOURCE"
            lead.created_at = now - timedelta(days=10)
            lead.converted_on = now - timedelta(days=5)
            project = db.get(Project, tenant)
            project.client_id = tenant
            project.project_status = "completed"
            project.project_worth = 120 if tenant == 1 else 999999
            project.value_type = "monthly"
            project.project_start = now - timedelta(days=10)
            project.project_end = now - timedelta(days=5)
            interaction = db.get(Interaction, tenant)
            interaction.contact_date = now - timedelta(days=100)
            interaction.follow_up = now + timedelta(days=5)
            interaction.summary = "Own upcoming" if tenant == 1 else "FOREIGN-TASK"
            db.add(Interaction(tenant_id=tenant, lead_id=tenant,
                contact_date=now - timedelta(days=100), follow_up=now-timedelta(days=1),
                summary="Own overdue" if tenant == 1 else "FOREIGN-OVERDUE"))
            db.add(Subscription(tenant_id=tenant, client_id=tenant, created_by=tenant,
                plan_name="Own plan" if tenant == 1 else "FOREIGN-PLAN", price=240 if tenant == 1 else 999999,
                billing_cycle="yearly", status="active", start_date=now-timedelta(days=10),
                renewal_date=now+timedelta(days=10)))
        db.commit()
    return crm


@pytest.mark.parametrize("restricted", [False, True])
def test_every_report_has_direct_and_http_tenant_boundaries_and_pure_reads(report_data, restricted):
    call, factory, _ = report_data
    runtime = auth_utils.SessionLocal if restricted else factory
    with runtime() as db:
        service = ReportService(db, Principal(1, 1, frozenset({"admin"})))
        before = db.query(ActivityLog).count()
        direct = {name: getattr(service, name)() for name in REPORTS}
        assert db.query(ActivityLog).count() == before
        assert not db.new and not db.dirty
        for name, path in REPORTS.items():
            serialized = json.dumps(direct[name])
            assert not any(secret in serialized for secret in ("FOREIGN", "Private client 2", "Private lead 2", "b@example.test", "999999")), name
            status, raw = call("GET", "/api/reports" + path)
            assert status == 200, (name, raw)
            assert json.loads(raw) == direct[name], name
        assert direct["get_reports"] == {"lead_count": 1, "converted_leads": 1, "project_count": 1,
            "won_projects": 1, "lost_projects": 0, "total_won_value": 120}
        assert direct["sales_pipeline"]["projects"][0]["count"] == 1
        assert direct["conversion_rate_report"]["overall"]["avg_days_to_convert"] == 5
        assert direct["project_performance_report"]["avg_duration_days"] == 5
        assert direct["revenue_by_client"]["clients"][0]["mrr"] == 120
        assert direct["revenue_forecast_report"]["total_weighted_forecast"] == 1440
        assert direct["subscription_income_report"]["mrr"] == 20
        assert direct["upcoming_renewals_report"]["total"] == 1
        assert direct["upcoming_tasks_report"]["upcoming_tasks"][0]["entity_name"] == "Private client 1"
        assert direct["follow_up_report"]["overdue_follow_ups"][0]["entity_name"] == "Private lead 1"
        assert direct["follow_up_report"]["inactive_clients"][0]["client_id"] == 1
        assert direct["converted_leads_report"]["converted_leads"][0]["assigned_to"] == "a@example.test"


def test_reports_require_authenticated_admin_service_and_current_http_roles(crm):
    call, factory, _ = crm
    with factory() as db:
        with pytest.raises(TypeError):
            ReportService(db, None)
        with pytest.raises(PermissionError):
            ReportService(db, Principal(3, 1, frozenset()))
    with factory() as db:
        db.get(User, 1).roles = []
        db.commit()
    for path in REPORTS.values():
        assert call("GET", "/api/reports" + path)[0] == 403


def test_report_joins_and_relationships_do_not_leak_without_rls(report_data):
    _, factory, _ = report_data
    now = datetime.utcnow()
    with factory() as db:
        db.get(Lead, 1).assigned_to = 2
        db.add_all([
            Project(tenant_id=1, created_by=1, client_id=2, project_name="Broken", project_status="completed", project_worth=999999),
            Subscription(tenant_id=1, created_by=1, client_id=2, plan_name="Broken", price=999999,
                         billing_cycle="yearly", status="active", start_date=now, renewal_date=now+timedelta(days=1)),
            Interaction(tenant_id=2, client_id=1, summary="FOREIGN-RECENT", contact_date=now, follow_up=now-timedelta(days=1)),
            Interaction(tenant_id=1, client_id=2, summary="Broken parent", contact_date=now, follow_up=now+timedelta(days=1)),
            Interaction(tenant_id=1, client_id=1, lead_id=1, summary="Broken cardinality", contact_date=now, follow_up=now+timedelta(days=1)),
        ])
        db.commit()
    with factory() as db:
        service = ReportService(db, Principal(1, 1, frozenset({"admin"})))
        assert service.sales_pipeline()["projects"][0]["count"] == 1
        assert service.revenue_forecast_report()["total_weighted_forecast"] == 1440
        assert service.subscription_income_report()["mrr"] == 20
        assert service.upcoming_renewals_report()["total"] == 1
        assert len(service.upcoming_tasks_report()["upcoming_tasks"]) == 1
        assert service.client_retention_report()["active_with_recent_interactions"] == 0
        assert len(service.follow_up_report()["inactive_clients"]) == 1
        assert service.converted_leads_report()["converted_leads"][0]["assigned_to"] is None
        assert "b@example.test" not in json.dumps(service.conversion_rate_report())
        assert service.sales_pipeline(user_filter=2)["leads"] == []
        assert service.upcoming_tasks_report(user_filter=2)["upcoming_tasks"] == []


def test_reports_validate_ranges_and_bounds_and_preserve_summary(crm):
    call, _, _ = crm
    for path in ("", "/pipeline", "/lead-source", "/conversion-rate", "/revenue-by-client",
                 "/user-activity", "/client-retention", "/project-performance", "/subscriptions/income", "/converted-leads"):
        for query in ("start_date=bad", "start_date=2026-09-10&end_date=2026-09-01"):
            assert call("GET", "/api/reports"+path+"?"+query)[0] == 400, path
    for path in ("/pipeline?user_id=-1", "/pipeline?user_id=abc", "/revenue-by-client?limit=201",
                 "/upcoming-tasks?days=-1", "/upcoming-tasks?user_id=0", "/follow-ups?inactive_days=1000000000",
                 "/subscriptions/upcoming-renewals?days=no", "/sales-activity?page=0"):
        assert call("GET", "/api/reports"+path)[0] == 400, path
    assert call("POST", "/api/reports/summary", [])[0] == 400
    assert call("POST", "/api/reports/summary", {"start_date": [1]})[0] == 400
    assert call("POST", "/api/reports/summary", {"start_date": False})[0] == 400
    assert json.loads(call("POST", "/api/reports/summary", {})[1]) == json.loads(call("GET", "/api/reports")[1])
    with auth_utils.SessionLocal() as db:
        service = ReportService(db, Principal(1, 1, frozenset({"admin"})))
        for method, kwargs in (("sales_pipeline", {"user_filter": True}), ("revenue_by_client", {"limit": "1"}),
                               ("upcoming_tasks_report", {"days_ahead": False}), ("sales_activity_report", {"page": -1})):
            with pytest.raises(ValueError):
                getattr(service, method)(**kwargs)


def test_historical_deleted_conversion_and_date_contract(report_data):
    call, factory, _ = report_data
    with factory() as db:
        db.get(Lead, 1).deleted_at = datetime.utcnow()
        db.commit()
    with auth_utils.SessionLocal() as db:
        service = ReportService(db, Principal(1, 1, frozenset({"admin"})))
        assert service.sales_pipeline()["leads"] == []
        assert service.lead_source_report()["sources"][0]["converted"] == 1
        assert service.converted_leads_report()["total"] == 1
        assert service.get_reports(start_date="2000-01-01T00:00:00Z", end_date="2000-01-02T00:00:00Z")["project_count"] == 0
        # Pipeline's user filter historically applies to leads only.
        assert service.sales_pipeline(user_filter=2)["projects"][0]["count"] == 1
        assert service.sales_activity_report(user_id=2)["events"] == []
