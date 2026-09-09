import os
os.environ.setdefault("SECRET_KEY", "isolated-test-only")
os.environ.setdefault("DATABASE_URL", "sqlite:///sales-test-unused.db")

from datetime import datetime
from types import SimpleNamespace
import asyncio
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from quart import Quart, request
from app.database import Base
from app.models import User, Lead, Client, Project, Interaction, ActivityLog, ActivityType
from app.utils.sales_activity_report import build_report, date_bounds
from app.utils.sales_audit import register_sales_audit, log_bulk_deletion


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        db.add_all([User(id=1, tenant_id=1, email="seller@test", password_hash="x", is_active=False),
                    User(id=2, tenant_id=1, email="owner@test", password_hash="x"),
                    User(id=3, tenant_id=2, email="other@test", password_hash="x")])
        db.commit()
        yield db
    engine.dispose()


def test_creation_dates_actor_inactive_users_and_tenant_boundary(session):
    session.add_all([
        Lead(tenant_id=1, created_by=1, assigned_to=2, name="Last second", created_at=datetime(2026,9,9,23,59,59)),
        Lead(tenant_id=1, created_by=1, name="Next day", created_at=datetime(2026,9,10)),
        Client(tenant_id=1, created_by=1, assigned_to=2, name="Created client", created_at=datetime(2026,9,3)),
        Project(tenant_id=1, created_by=1, assigned_to=2, project_name="Created project", project_status="active", created_at=datetime(2026,9,5)),
        Lead(tenant_id=2, created_by=3, name="Other company", created_at=datetime(2026,9,5)),
        Interaction(tenant_id=1, summary="Legacy call", contact_date=datetime(2026,9,6)),
    ])
    session.commit()
    report = build_report(session, 1, "2026-09-03", "2026-09-09")
    seller = next(u for u in report["users"] if u["user_id"] == 1)
    assert seller["leads_created"] == seller["clients_created"] == seller["projects_created"] == 1
    assert seller["interactions"] == 0 and seller["total"] == 3
    assert report["unattributed_total"] == 1 and report["total"] == 4
    assert next(u for u in report["users"] if u["user_id"] == 2)["total"] == 0
    assert "Other company" not in str(report)
    assert build_report(session, 1, "2026-09-09", "2026-09-09")["total"] == 1
    assert build_report(session, 1, user_id=3)["total"] == 0


@pytest.mark.parametrize("start,end", [("2026-09-10", "2026-09-01"), ("bad", None), (None, "2026-02-30"), ("2026-9-1", None)])
def test_bad_dates(start, end):
    with pytest.raises(ValueError):
        date_bounds(start, end)


def test_transactional_actor_audit_dedup_pagination_and_rollback(session):
    register_sales_audit()
    app = Quart(__name__)
    async def exercise():
        async with app.test_request_context("/api/interactions", method="POST"):
            request.user = SimpleNamespace(id=2, tenant_id=1)
            lead = Lead(tenant_id=1, created_by=2, assigned_to=1, name="New lead")
            interaction = Interaction(tenant_id=1, summary="New call")
            session.add_all([lead, interaction])
            session.commit()
            report = build_report(session, 1)
            owner = next(u for u in report["users"] if u["user_id"] == 2)
            assert owner["leads_created"] == owner["interactions"] == 1
            assert report["total"] == 2  # No record/log duplicate
            assert build_report(session, 1, page=2, per_page=1)["events"]
            lead.name = "Rolled back"
            session.flush()
            session.rollback()
            assert build_report(session, 1)["total"] == 2
            interaction.summary = "Edited call"
            session.commit()
            session.delete(interaction)
            session.commit()
            report = build_report(session, 1)
            assert report["total"] == 4
            assert any(e["action"] == "deleted" for e in report["events"])
            log_bulk_deletion(session, session.query(Lead).filter(Lead.id == lead.id))
            session.query(Lead).filter(Lead.id == lead.id).delete(synchronize_session=False)
            session.commit()
            assert build_report(session, 1)["total"] == 5
    asyncio.run(exercise())


def test_soft_delete_and_restore_are_recorded_once(session):
    register_sales_audit()
    app = Quart(__name__)
    async def exercise():
        async with app.test_request_context("/api/leads/1", method="DELETE"):
            request.user = SimpleNamespace(id=2, tenant_id=1)
            lead = Lead(tenant_id=1, created_by=2, name="Soft delete")
            session.add(lead)
            session.commit()
            lead.deleted_at = datetime.utcnow()
            lead.deleted_by = 2
            session.commit()
            report = build_report(session, 1, user_id=2)
            assert report["users"][0]["deletions"] == 1
            assert report["total"] == 2
            lead.deleted_at = None
            session.commit()
            assert build_report(session, 1, user_id=2)["users"][0]["edits"] == 1
    asyncio.run(exercise())


def test_report_http_requires_auth_and_current_admin_role(session, monkeypatch):
    import app.routes.reports as reports
    import app.utils.auth_utils as auth
    app = Quart(__name__)
    app.register_blueprint(reports.reports_bp)
    monkeypatch.setattr(auth, "SessionLocal", lambda: session)
    monkeypatch.setattr(reports, "SessionLocal", lambda: session)
    # A stale admin claim cannot promote a user with no current admin role.
    monkeypatch.setattr(auth, "decode_token", lambda _: {"sub": 2, "roles": ["admin"]})
    async def exercise():
        client = app.test_client()
        assert (await client.get("/api/reports/sales-activity")).status_code == 401
        assert (await client.get("/api/reports/sales-activity", headers={"Authorization": "Bearer test"})).status_code == 403
    asyncio.run(exercise())
