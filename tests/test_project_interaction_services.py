from datetime import datetime
import json

import pytest
from test_security_boundaries import crm as crm
from sqlalchemy.exc import SQLAlchemyError

from app.models import Project, Interaction, Client, ActivityLog
from app.services.principal import Principal
from app.services.projects import ProjectService, RecordNotFound
from app.services.interactions import InteractionService
from app.schemas.projects import (
    ProjectCreateSchema,
    ProjectUpdateSchema,
    ProjectAssignSchema,
)
from app.schemas.interactions import InteractionCreateSchema, InteractionUpdateSchema

ADMIN = Principal(1, 1, frozenset({"admin"}))
ORDINARY = Principal(3, 1, frozenset())


@pytest.mark.parametrize(
    "method,args",
    [
        ("detail", (2,)),
        ("update", (2, ProjectUpdateSchema(notes="deny"))),
        ("delete", (2,)),
        ("restore", (2,)),
        ("purge", (2,)),
        ("assign", (2, ProjectAssignSchema(assigned_to=1))),
        ("interaction_link", (2,)),
    ],
)
def test_project_service_rejects_foreign_without_http(crm, method, args):
    _, factory, _ = crm
    with factory() as db:
        with pytest.raises(RecordNotFound):
            getattr(ProjectService(db, ADMIN), method)(*args)


@pytest.mark.parametrize(
    "method,args",
    [
        ("list_all", ()),
        ("assign", (1, ProjectAssignSchema(assigned_to=3))),
        ("purge", (1,)),
        ("bulk_delete", ([1, 2],)),
        ("bulk_purge", ([1, 2],)),
    ],
)
def test_project_service_admin_boundary_without_http(crm, method, args):
    _, factory, _ = crm
    with factory() as db:
        with pytest.raises(PermissionError):
            getattr(ProjectService(db, ORDINARY), method)(*args)


@pytest.mark.parametrize(
    "method,args",
    [
        ("update", (2, InteractionUpdateSchema(notes="deny"))),
        ("delete", (2,)),
        ("calendar", (2,)),
        ("complete", (2,)),
        ("transfer", (2, 1)),
    ],
)
def test_interaction_service_rejects_foreign_without_http(crm, method, args):
    _, factory, _ = crm
    with factory() as db:
        with pytest.raises(RecordNotFound):
            getattr(InteractionService(db, ADMIN), method)(*args)


def test_project_assignment_overrides_inheritance_in_lists_and_mutations(crm):
    _, factory, _ = crm
    with factory() as db:
        db.get(Client, 1).assigned_to = 3
        project = db.get(Project, 1)
        project.client_id = 1
        project.assigned_to = 1
        db.add(
            Interaction(
                tenant_id=1,
                project_id=1,
                summary="Project conversation",
                contact_date=datetime.utcnow(),
            )
        )
        db.commit()
    with factory() as db:
        projects, interactions = (
            ProjectService(db, ORDINARY),
            InteractionService(db, ORDINARY),
        )
        assert projects.by_client(1) == []
        with pytest.raises(RecordNotFound):
            projects.detail(1)
        assert interactions.list_visible(project_id=1)["total"] == 0
    with factory() as db:
        db.get(Project, 1).assigned_to = 3
        db.get(Client, 1).assigned_to = 1
        db.commit()
    with factory() as db:
        projects, interactions = (
            ProjectService(db, ORDINARY),
            InteractionService(db, ORDINARY),
        )
        assert projects.detail(1)["id"] == 1
        assert projects.list_mine()["total"] == 1
        assert interactions.list_visible(project_id=1)["total"] == 1
        projects.update(1, ProjectUpdateSchema(notes="Assignee can edit project"))
        row = interactions.list_visible(project_id=1)["interactions"][0]
        interactions.complete(row["id"])
        db.rollback()


def test_project_interaction_service_pure_read_transfer_and_rollback(crm):
    _, factory, _ = crm
    with factory() as db:
        projects, interactions = (
            ProjectService(db, ADMIN),
            InteractionService(db, ADMIN),
        )
        created = projects.create(
            ProjectCreateSchema(project_name="Rollback", client_id=1, tenant_id=2)
        )
        project_id = created["id"]
        assert db.get(Project, project_id).tenant_id == 1
        before = db.query(ActivityLog).count()
        assert projects.detail(project_id)["project_name"] == "Rollback"
        assert db.query(ActivityLog).count() == before
        item = interactions.create(
            InteractionCreateSchema(
                project_id=project_id,
                summary="Follow up",
                contact_date=datetime(2026, 9, 12),
                follow_up=datetime(2026, 9, 13),
            )
        )
        interaction_id = item["id"]
        assert b"BEGIN:VCALENDAR" in interactions.calendar(interaction_id)
        with pytest.raises(RecordNotFound):
            interactions.update(
                interaction_id, InteractionUpdateSchema(project_id=None, client_id=2)
            )
        interactions.update(
            interaction_id, InteractionUpdateSchema(project_id=None, lead_id=1)
        )
        assert interactions.transfer(1, 1)["transferred"] == 1
        assert db.get(Interaction, interaction_id).client_id == 1
        db.rollback()
        assert db.get(Project, project_id) is None
        assert db.get(Interaction, interaction_id) is None


def test_nested_project_user_is_tenant_scoped_without_http(crm):
    _, factory, _ = crm
    with factory() as db:
        db.get(Project, 1).assigned_to = 2
        db.commit()
    with factory() as db:
        result = ProjectService(db, ADMIN).detail(1)
        assert result["assigned_to_name"] is None
        assert "b@example.test" not in str(result)


def test_project_assignment_mail_after_commit_and_no_mail_on_failure(crm, monkeypatch):
    from app.routes import projects

    call, factory, _ = crm
    notices = []

    async def notification(**kwargs):
        with factory() as db:
            assert db.get(Project, 1).assigned_to == 3
        notices.append(kwargs)
        raise RuntimeError("Synthetic mail failure")

    monkeypatch.setattr(projects, "send_assignment_notification", notification)
    assert call("PUT", "/api/projects/1/assign", {"assigned_to": 2})[0] == 400
    assert call("PUT", "/api/projects/1/assign", {"assigned_to": 3})[0] == 200
    assert len(notices) == 1
    route_factory = projects.SessionLocal

    def failing_session():
        session = route_factory()

        def fail():
            raise SQLAlchemyError("PRIVATE detail")

        session.commit = fail
        return session

    monkeypatch.setattr(projects, "SessionLocal", failing_session)
    status, body = call("PUT", "/api/projects/1/assign", {"assigned_to": 1})
    assert status == 500 and "PRIVATE" not in body
    assert len(notices) == 1
    with factory() as db:
        assert db.get(Project, 1).assigned_to == 3


def test_project_interaction_http_lifecycle_and_purge_conflict(crm):
    call, factory, _ = crm
    if factory.kw["bind"].dialect.name == "postgresql":
        from test_security_boundaries import _parent_rules

        _parent_rules(factory)
    status, body = call("POST", "/api/projects", {"project_name": "Lifecycle"})
    assert status == 201
    project_id = json.loads(body)["id"]
    path = f"/api/projects/{project_id}"
    status, body = call(
        "POST",
        "/api/interactions",
        {
            "project_id": project_id,
            "summary": "Follow up",
            "contact_date": "2026-09-12T12:00:00",
            "follow_up": "2026-09-13T12:00:00",
        },
    )
    assert status == 201
    interaction_id = json.loads(body)["id"]
    assert call("GET", path)[0] == 200
    with factory() as db:
        assert (
            db.query(ActivityLog)
            .filter_by(entity_type="project", entity_id=project_id, action="viewed")
            .count()
            == 1
        )
    assert call("GET", f"/api/interactions/{interaction_id}/calendar.ics")[0] == 200
    assert call("PUT", f"/api/interactions/{interaction_id}/complete")[0] == 200
    assert call("DELETE", path)[0] == 200
    assert (
        call("GET", f"/api/interactions?project_id={project_id}")[1].find("Follow up")
        == -1
    )
    if factory.kw["bind"].dialect.name == "postgresql":
        assert call("DELETE", path + "/purge")[0] == 409
        assert (
            call("POST", "/api/projects/bulk-purge", {"project_ids": [project_id]})[0]
            == 409
        )
    assert call("PUT", path + "/restore")[0] == 200
    assert call("DELETE", f"/api/interactions/{interaction_id}")[0] == 200
    assert (
        call("POST", "/api/projects/bulk-delete", {"project_ids": [project_id, 2]})[0]
        == 200
    )
    assert (
        call("POST", "/api/projects/bulk-purge", {"project_ids": [project_id, 2]})[0]
        == 200
    )
    assert call("GET", "/api/projects/2", user=2)[0] == 200


def test_admin_email_filters_do_not_match_foreign_related_users(crm):
    _, factory, _ = crm
    with factory() as db:
        db.get(Client, 1).assigned_to = 2
        db.get(Project, 1).client_id = 1
        db.commit()
    with factory() as db:
        assert (
            ProjectService(db, ADMIN).list_all(user_email="b@example.test")["total"]
            == 0
        )
        assert (
            InteractionService(db, ADMIN).list_all(user_email="b@example.test")["total"]
            == 0
        )


@pytest.mark.parametrize("parent_model", [Client, Project])
def test_interaction_operations_deny_deleted_parents(crm, parent_model):
    _, factory, _ = crm
    with factory() as db:
        if parent_model is Project:
            interaction = db.get(Interaction, 1)
            interaction.client_id = None
            interaction.project_id = 1
        db.get(parent_model, 1).deleted_at = datetime.utcnow()
        db.commit()
    with factory() as db:
        service = InteractionService(db, ADMIN)
        assert service.list_visible()["total"] == 0
        for method in ("delete", "complete", "calendar"):
            with pytest.raises(RecordNotFound):
                getattr(service, method)(1)


def test_project_interaction_parent_moves_refresh_loaded_relationships(crm):
    _, factory, _ = crm
    with factory() as db:
        db.get(Project, 1).client_id = 1
        db.commit()
    with factory() as db:
        projects, interactions = (
            ProjectService(db, ADMIN),
            InteractionService(db, ADMIN),
        )
        assert projects.detail(1)["client_name"] == "Private client 1"
        updated = projects.update(1, ProjectUpdateSchema(client_id=None, lead_id=1))
        assert (
            updated["client_name"] is None and updated["lead_name"] == "Private lead 1"
        )
        assert (
            interactions.list_visible(client_id=1)["interactions"][0]["client_name"]
            == "Private client 1"
        )
        interactions.update(1, InteractionUpdateSchema(client_id=None, lead_id=1))
        moved = interactions.list_visible(lead_id=1)["interactions"][0]
        assert moved["client_name"] is None and moved["lead_name"] == "Private lead 1"
        interactions.transfer(1, 1)
        moved_back = interactions.list_visible(client_id=1)["interactions"][0]
        assert (
            moved_back["client_name"] == "Private client 1"
            and moved_back["lead_name"] is None
        )
        db.rollback()
