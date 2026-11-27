"""Client boundaries outside HTTP, plus the existing web contract and transactions."""
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy.exc import SQLAlchemyError

from test_security_boundaries import crm
from app.models import ActivityLog, Client, Interaction, User
from app.routes import clients
from app.schemas.clients import ClientAssignSchema
from app.services.clients import ClientService, RecordNotFound
from app.services.principal import Principal
from app.utils import auth_utils


ADMIN = Principal(1, 1, frozenset({"admin"}))
ORDINARY = Principal(3, 1, frozenset())


@pytest.mark.parametrize("restricted", [False, True])
def test_lists_keep_personal_assigned_admin_and_trash_contracts(crm, restricted):
    _, admin_factory, _ = crm
    with admin_factory() as db:
        db.get(Client, 1).assigned_to = 3
        db.add_all([
            Client(id=10, tenant_id=1, created_by=3, name="Owned"),
            Client(id=11, tenant_id=1, created_by=3, assigned_to=1, name="Reassigned"),
            Client(id=12, tenant_id=1, created_by=1, name="Hidden"),
        ])
        db.commit()
    factory = auth_utils.SessionLocal if restricted else admin_factory
    with factory() as db:
        service = ClientService(db, ORDINARY)
        assert {r["id"] for r in service.list_mine()["clients"]} == {1, 10}
        assert [r["id"] for r in service.list_assigned()] == [1]
        assert service.list_assigned()[0]["assigned_to_name"] == "ordinary@example.test"
        # Creation still grants detail/trash access after reassignment.
        assert service.detail(11)["name"] == "Reassigned"
        service.delete(11)
        assert [r["id"] for r in service.list_trash()] == [11]
        db.rollback()
    with factory() as db:
        service = ClientService(db, ADMIN)
        assert {r["id"] for r in service.list_mine()["clients"]} == {11, 12}
        assert service.list_all()["total"] == 4
        assert service.list_all(user_email="b@example.test")["total"] == 0
        assert service.list_all(user_email="ordinary@example.test")["total"] == 3
        assert service.list_all(per_page=1, page=5)["clients"] == []
        assert service.list_all(sort_order="invalid")["sort_order"] == "newest"


@pytest.mark.parametrize("restricted", [False, True])
def test_activity_filters_sorting_counts_and_pagination(crm, restricted):
    _, admin_factory, _ = crm
    now = datetime.utcnow()
    with admin_factory() as db:
        db.get(Client, 1).created_at = now - timedelta(days=20)
        db.get(Interaction, 1).contact_date = now - timedelta(days=10)
        db.add_all([
            Client(id=10, tenant_id=1, created_by=1, name="Older", created_at=now-timedelta(days=20)),
            Client(id=11, tenant_id=1, created_by=1, name="Recent", created_at=now-timedelta(days=20)),
            Client(id=12, tenant_id=1, created_by=1, name="Empty", created_at=now),
        ])
        db.flush()
        db.add_all([
            Interaction(tenant_id=1, client_id=10, summary="Old", contact_date=now-timedelta(days=100)),
            Interaction(tenant_id=1, client_id=11, summary="Latest", contact_date=now-timedelta(days=1)),
            Interaction(tenant_id=1, client_id=11, summary="Earlier", contact_date=now-timedelta(days=2)),
        ])
        db.commit()
    factory = auth_utils.SessionLocal if restricted else admin_factory
    with factory() as db:
        service = ClientService(db, ADMIN)
        before = db.query(ActivityLog).count()
        for method in (service.list_all, service.list_mine):
            active = method(sort_order="activity", activity_filter="active", per_page=1)
            assert active["total"] == 2 and active["clients"][0]["id"] == 11
            assert active["clients"][0]["interaction_count"] == 2
            assert active["clients"][0]["last_interaction_date"] == (now-timedelta(days=1)).isoformat() + "Z"
            assert method(sort_order="activity", activity_filter="active", per_page=1, page=2)["clients"][0]["id"] == 1
            assert [r["id"] for r in method(sort_order="activity")["clients"]] == [11, 1, 10, 12]
            assert {r["id"] for r in method(activity_filter="inactive")["clients"]} == {10, 12}
            assert [r["id"] for r in method(activity_filter="new")["clients"]] == [12]
            assert method(sort_order="alphabetical")["clients"][0]["name"] == "Empty"
            assert [r["id"] for r in method(sort_order="oldest")["clients"]] == [1, 10, 11, 12]
        assert db.query(ActivityLog).count() == before
        assert not db.new and not db.dirty


def test_malformed_users_and_interactions_cannot_affect_lists_without_http_or_rls(crm):
    _, factory, _ = crm
    now = datetime.utcnow()
    with factory() as db:
        db.get(Client, 1).assigned_to = 2
        db.get(Interaction, 1).contact_date = now - timedelta(days=100)
        db.add_all([
            Interaction(tenant_id=2, client_id=1, summary="Foreign", contact_date=now),
            Interaction(tenant_id=1, client_id=1, lead_id=1, summary="Malformed", contact_date=now),
            Client(id=10, tenant_id=1, created_by=2, name="Malformed creator"),
        ])
        db.commit()
    # Administrator fixture connection deliberately bypasses RLS. The service must
    # still prevent foreign names, stats, filters and sort order from leaking.
    with factory() as db:
        service = ClientService(db, ADMIN)
        result = service.list_all(sort_order="activity")
        row = next(r for r in result["clients"] if r["id"] == 1)
        assert row["interaction_count"] == 1
        assert row["assigned_to_name"] == "a@example.test"
        assert "b@example.test" not in json.dumps(result)
        assert service.list_all(user_email="b@example.test")["total"] == 0
        assert service.list_all(activity_filter="active")["total"] == 0
        assert service.list_all(activity_filter="inactive")["total"] == 2


@pytest.mark.parametrize("operation,args", [
    ("list_all", ()), ("bulk_delete", ([1],)),
    ("assign", (1, ClientAssignSchema(assigned_to=3))),
])
def test_admin_operations_reject_ordinary_direct_call(crm, operation, args):
    with auth_utils.SessionLocal() as db:
        service = ClientService(db, ORDINARY)
        with pytest.raises(PermissionError):
            getattr(service, operation)(*args)


def test_assignment_authorization_and_rollback(crm):
    _, factory, _ = crm
    with factory() as db:
        db.get(User, 3).is_active = False
        db.commit()
    with auth_utils.SessionLocal() as db:
        service = ClientService(db, ADMIN)
        with pytest.raises(TypeError):
            service.assign(1, {"assigned_to": 3})
        for client_id in (2, 999):
            with pytest.raises(RecordNotFound):
                service.assign(client_id, ClientAssignSchema(assigned_to=1))
        for user_id in (2, 3, 999):
            with pytest.raises(ValueError):
                service.assign(1, ClientAssignSchema(assigned_to=user_id))
        notice = service.assign(1, ClientAssignSchema(assigned_to=1))
        assert notice == {"to_email": "a@example.test", "entity_type": "client", "entity_name": "Private client 1"}
        assert db.get(Client, 1).updated_by == 1
        db.rollback()
        assert db.get(Client, 1).assigned_to is None
        service.delete(1)
        with pytest.raises(RecordNotFound):
            service.assign(1, ClientAssignSchema(assigned_to=1))


def test_bulk_deletion_is_tenant_scoped_transactional_and_audited_once(crm):
    _, factory, _ = crm
    with auth_utils.SessionLocal() as db:
        service = ClientService(db, ADMIN)
        for ids in (None, [], [True], ["1"], [0], [-1], [1, "2"]):
            with pytest.raises(ValueError):
                service.bulk_delete(ids)
        assert service.bulk_delete([1, 1, 2, 999]) == 1
        assert db.get(Client, 1).deleted_by == 1
        assert [r["id"] for r in service.list_trash()] == [1]
        events = db.query(ActivityLog).filter_by(entity_type="client", entity_id=1).all()
        assert len(events) == 1 and events[0].action.value == "deleted"
        assert (events[0].user_id, events[0].tenant_id) == (1, 1)
        assert service.bulk_delete([1]) == 0
        db.rollback()
        assert db.get(Client, 1).deleted_at is None
        assert db.query(ActivityLog).filter_by(entity_type="client", entity_id=1).count() == 0
        assert service.bulk_delete([1, 2]) == 1
        db.commit()
    with factory() as db:
        assert db.get(Client, 2).deleted_at is None
        assert db.get(Client, 1).deleted_by == 1


def test_http_list_shapes_validation_and_bulk_contract(crm):
    call, _, _ = crm
    for prefix in ("/api/clients", "/api/clients/all"):
        for query in ("page=0", "page=oops", "per_page=0", "per_page=201", "page=-1"):
            assert call("GET", prefix + "?" + query)[0] == 400
    status, raw = call("GET", "/api/clients")
    result = json.loads(raw)
    assert status == 200 and result["total"] == 1
    common = {"id", "name", "email", "phone", "phone_label", "secondary_phone", "secondary_phone_label",
              "contact_person", "contact_title", "type", "status", "assigned_to_name"}
    stats = {"created_at", "interaction_count", "last_interaction_date"}
    assert set(result["clients"][0]) == common | stats | {"address", "city", "state", "zip", "notes", "assigned_to"}
    all_clients = json.loads(call("GET", "/api/clients/all")[1])
    assert set(all_clients["clients"][0]) == common | stats | {"created_by", "created_by_name"}
    assert call("GET", "/api/clients/all", user=3)[0] == 403
    for body in ([], {}, {"client_ids": []}, {"client_ids": [True]}, {"client_ids": ["1"]}):
        assert call("POST", "/api/clients/bulk-delete", body)[0] == 400
    assert call("POST", "/api/clients/bulk-delete", {"client_ids": [1]}, user=3)[0] == 403
    status, raw = call("POST", "/api/clients/bulk-delete", {"client_ids": [1, 2]})
    assert status == 200 and json.loads(raw)["message"] == "1 client(s) deleted"
    assert [r["id"] for r in json.loads(call("GET", "/api/clients/trash")[1])] == [1]


def test_http_assignment_notifies_after_commit_and_survives_mail_failure(crm, monkeypatch):
    call, factory, _ = crm
    notices = []

    async def notify(**kwargs):
        with factory() as db:
            assert db.get(Client, 1).assigned_to == 3
        notices.append(kwargs)
        raise RuntimeError("Synthetic mail failure")

    monkeypatch.setattr(clients, "send_assignment_notification", notify)
    for body in ([], {}, {"assigned_to": True}, {"assigned_to": "3"}, {"assigned_to": 0}):
        assert call("PUT", "/api/clients/1/assign", body)[0] == 400
    assert call("PUT", "/api/clients/1/assign", {"assigned_to": 3}, user=3)[0] == 403
    assert call("PUT", "/api/clients/2/assign", {"assigned_to": 3})[0] == 404
    assert call("PUT", "/api/clients/1/assign", {"assigned_to": 2})[0] == 400
    assert notices == []
    assert call("PUT", "/api/clients/1/assign", {"assigned_to": 3})[0] == 200
    assert len(notices) == 1 and notices[0]["assigned_by"] == "a@example.test"
    assigned = json.loads(call("GET", "/api/clients/assigned", user=3)[1])
    assert assigned[0]["id"] == 1 and assigned[0]["assigned_to_name"] == "ordinary@example.test"


def test_failed_commit_never_notifies_or_persists_assignment(crm, monkeypatch):
    call, factory, _ = crm
    route_factory = clients.SessionLocal
    notices = []

    async def notify(**kwargs):
        notices.append(kwargs)

    def failing_session():
        session = route_factory()

        def fail():
            raise SQLAlchemyError("Synthetic private database detail")

        session.commit = fail
        return session

    monkeypatch.setattr(clients, "SessionLocal", failing_session)
    monkeypatch.setattr(clients, "send_assignment_notification", notify)
    status, body = call("PUT", "/api/clients/1/assign", {"assigned_to": 3})
    assert status == 500 and "Synthetic private" not in body
    assert notices == []
    with factory() as db:
        assert db.get(Client, 1).assigned_to is None
        assert db.query(ActivityLog).filter_by(entity_type="client", entity_id=1).count() == 0


def test_conversion_keeps_type_status_and_origin_after_lead_is_deleted(crm):
    """Converted clients keep the lead's type, start active, and show their origin."""
    from app.models import Lead
    from app.schemas.clients import ClientCreateSchema, ClientUpdateSchema
    from app.services.leads import LeadService
    _, admin_factory, _ = crm
    with admin_factory() as db:
        db.add(Lead(id=40, tenant_id=1, created_by=3, name="Test Company", type="Manufacturing",
                    lead_source="Referral", lead_status="won"))
        db.commit()
        service = ClientService(db, ORDINARY)
        client_id = service.create(ClientCreateSchema(name="Test Company", source_lead_id=40))
        LeadService(db, ORDINARY).delete(40)  # the web conversion's last step
        db.commit()
        detail = service.detail(client_id)
        assert (detail["type"], detail["status"]) == ("Manufacturing", "active")
        assert detail["lead_origin"]["lead_source"] == "Referral"
        assert detail["lead_origin"]["converted_on"] is not None
        # An explicit choice still wins over the conversion defaults.
        other = service.create(ClientCreateSchema(name="Other", type="Retail", status="prospect"))
        assert (service.detail(other)["type"], service.detail(other)["status"]) == ("Retail", "prospect")
        # Editing status is visible on the detail page and in lists.
        service.update(client_id, ClientUpdateSchema(status="inactive"))
        db.commit()
        assert service.detail(client_id)["status"] == "inactive"
        assert {r["id"]: r["status"] for r in service.list_mine()["clients"]}[client_id] == "inactive"


def test_conversion_moves_the_leads_records_and_deletes_it_in_one_transaction(crm):
    """Projects, contacts and interactions follow a converted lead to its client.

    Left on the deleted lead, projects and contacts were hidden from everyone.
    """
    from app.models import Contact, Lead, Project
    from app.schemas.clients import ClientCreateSchema
    from app.services.interactions import InteractionService
    from app.services.leads import LeadService
    from app.services.projects import ProjectService
    _, admin_factory, _ = crm
    with admin_factory() as db:
        db.add(Lead(id=41, tenant_id=1, created_by=3, name="Synthetic Lead Co", lead_status="qualified"))
        db.flush()
        db.add_all([Project(id=41, tenant_id=1, created_by=3, lead_id=41, project_name="Synthetic job",
                            project_status="pending"),
                    Contact(id=41, tenant_id=1, lead_id=41, first_name="Synthetic"),
                    Interaction(id=41, tenant_id=1, lead_id=41, summary="Synthetic call")])
        db.commit()
        client_id = ClientService(db, ORDINARY).create(ClientCreateSchema(name="Synthetic Lead Co", source_lead_id=41))
        db.commit()
        lead = db.get(Lead, 41)
        assert lead.deleted_at is not None and lead.lead_status == "won" and lead.converted_on is not None
        assert (db.get(Project, 41).client_id, db.get(Project, 41).lead_id) == (client_id, None)
        assert (db.get(Contact, 41).client_id, db.get(Contact, 41).lead_id) == (client_id, None)
        assert (db.get(Interaction, 41).client_id, db.get(Interaction, 41).lead_id) == (client_id, None)
        # The project stays visible to its owner under the new client.
        assert 41 in [p["id"] for p in ProjectService(db, ORDINARY).list_mine()["projects"]]
        # The web's later steps (transfer, then delete) succeed with nothing left to do.
        assert InteractionService(db, ORDINARY).transfer(41, client_id) == {"success": True, "transferred": 0}
        assert LeadService(db, ORDINARY).delete(41) is False
        # A second conversion of the same lead is refused, even once it is restored.
        with pytest.raises(RecordNotFound):
            ClientService(db, ORDINARY).create(ClientCreateSchema(name="Again", source_lead_id=41))
        LeadService(db, ORDINARY).restore(41)
        db.commit()
        with pytest.raises(ValueError, match="already converted"):
            ClientService(db, ORDINARY).create(ClientCreateSchema(name="Again", source_lead_id=41))


def test_a_failed_conversion_leaves_the_lead_and_its_records_untouched(crm, monkeypatch):
    from app.models import Lead, Project
    from app.schemas.clients import ClientCreateSchema
    _, admin_factory, _ = crm
    with admin_factory() as db:
        db.add(Lead(id=42, tenant_id=1, created_by=3, name="Synthetic Two", lead_status="qualified"))
        db.flush()
        db.add(Project(id=42, tenant_id=1, created_by=3, lead_id=42, project_name="Job two", project_status="pending"))
        db.commit()

        absorb = ClientService._absorb_lead

        def broken(self, lead, client_id):
            absorb(self, lead, client_id)  # Every move and the lead update are flushed...
            raise SQLAlchemyError("synthetic failure")  # ...then the conversion fails.
        monkeypatch.setattr(ClientService, "_absorb_lead", broken)
        with pytest.raises(SQLAlchemyError):
            ClientService(db, ORDINARY).create(ClientCreateSchema(name="Synthetic Two", source_lead_id=42))
        db.rollback()
        lead = db.get(Lead, 42)
        assert (lead.deleted_at, lead.lead_status, lead.converted_on) == (None, "qualified", None)
        assert (db.get(Project, 42).lead_id, db.get(Project, 42).client_id) == (42, None)
        assert db.query(Client).filter_by(source_lead_id=42).count() == 0
