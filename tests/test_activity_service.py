import json
from datetime import datetime, timedelta
import pytest
from app.models import Client, Lead, Project, Account, ActivityLog, ActivityType
from app.services.activity import ActivityService
from app.services.principal import Principal
from test_security_boundaries import crm as crm

USER = Principal(3, 1, frozenset())
ADMIN = Principal(1, 1, frozenset({"admin"}))


def log(db, kind, entity_id=1, user_id=3, tenant_id=1, offset=0):
    db.add(
        ActivityLog(
            tenant_id=tenant_id,
            user_id=user_id,
            entity_type=kind,
            entity_id=entity_id,
            action=ActivityType.viewed,
            description="Old name must not be used",
            timestamp=datetime(2026, 9, 12) + timedelta(seconds=offset),
        )
    )


def test_recent_activity_revokes_current_record_access(crm):
    call, factory, _ = crm
    with factory() as db:
        for model in (Client, Lead, Project):
            db.get(model, 1).assigned_to = 3
        for kind in ("client", "lead", "project", "account"):
            log(db, kind)
        log(db, "client", offset=1)
        db.commit()
    status, body = call("GET", "/api/activity/recent", user=3)
    assert status == 200 and len(json.loads(body)) == 4
    assert json.loads(body)[0]["entity_type"] == "client"
    with factory() as db:
        for model in (Client, Lead, Project):
            db.get(model, 1).assigned_to = 1
        db.commit()
    assert json.loads(call("GET", "/api/activity/recent", user=3)[1]) == []
    with factory() as db:
        assert ActivityService(db, USER).recent() == []
        assert db.query(ActivityLog).count() == 5


def test_recent_activity_project_assignment_overrides_parent(crm):
    _, factory, _ = crm
    with factory() as db:
        db.get(Client, 1).assigned_to = 3
        project = db.get(Project, 1)
        project.client_id = 1
        project.assigned_to = 1
        log(db, "project")
        db.commit()
    with factory() as db:
        assert ActivityService(db, USER).recent() == []
    with factory() as db:
        db.get(Project, 1).assigned_to = None
        db.commit()
    with factory() as db:
        assert len(ActivityService(db, USER).recent()) == 1


def test_recent_activity_scopes_logs_and_foreign_entities(crm):
    _, factory, _ = crm
    with factory() as db:
        log(db, "client", user_id=1)
        log(db, "lead", user_id=3)
        log(db, "project", user_id=1, tenant_id=2)
        log(db, "client", entity_id=2, user_id=1)
        log(db, "unknown", user_id=1)
        db.commit()
    with factory() as db:
        rows = ActivityService(db, ADMIN).recent()
        assert (
            len(rows) == 1
            and rows[0]["entity_type"] == "client"
            and rows[0]["entity_id"] == 1
        )


@pytest.mark.parametrize("model", [Client, Lead, Project])
def test_recent_activity_hides_deleted_records_even_for_admin(crm, model):
    _, factory, _ = crm
    kind = {Client: "client", Lead: "lead", Project: "project"}[model]
    with factory() as db:
        log(db, kind, user_id=1)
        if model is Client:
            log(db, "account", user_id=1)
        db.get(model, 1).deleted_at = datetime.utcnow()
        db.commit()
    with factory() as db:
        assert ActivityService(db, ADMIN).recent() == []


def test_recent_activity_malformed_parent_is_hidden(crm):
    _, factory, _ = crm
    with factory() as db:
        db.get(Account, 1).client_id = 2
        db.get(Project, 1).client_id = 2
        log(db, "account", user_id=1)
        log(db, "project", user_id=1)
        db.commit()
    with factory() as db:
        assert ActivityService(db, ADMIN).recent() == []


@pytest.mark.parametrize("value", ["bad", "0", "-1"])
def test_recent_activity_invalid_limits(crm, value):
    call, _, _ = crm
    assert call("GET", "/api/activity/recent?limit=" + value)[0] == 400
