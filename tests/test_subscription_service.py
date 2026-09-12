import json
from datetime import datetime
import pytest
from app.models import Client, Subscription
from app.schemas.subscriptions import SubscriptionCreateSchema, SubscriptionUpdateSchema
from app.services.subscriptions import SubscriptionService
from app.services.principal import Principal
from app.services.errors import RecordNotFound
from test_security_boundaries import crm as crm

ADMIN = Principal(1, 1, frozenset({"admin"}))
USER = Principal(3, 1, frozenset())
BODY = dict(
    client_id=1,
    plan_name="Service",
    price=12,
    billing_cycle="monthly",
    start_date="2024-01-31T12:00:00",
)


@pytest.fixture
def subscriptions(crm):
    call, factory, app = crm
    with factory() as db:
        for tenant in (1, 2):
            db.add(
                Subscription(
                    id=tenant,
                    tenant_id=tenant,
                    client_id=tenant,
                    created_by=tenant,
                    plan_name="Private " + str(tenant),
                    price=10,
                    billing_cycle="monthly",
                    start_date=datetime(2024, 1, 31),
                    renewal_date=datetime(2024, 2, 29),
                    status="active",
                )
            )
        db.commit()
    return crm


@pytest.mark.parametrize(
    "method,args",
    [
        ("detail", []),
        ("update", [SubscriptionUpdateSchema(notes="no")]),
        ("delete", []),
        ("renew", []),
    ],
)
def test_subscription_service_denies_foreign_and_unowned(subscriptions, method, args):
    _, factory, _ = subscriptions
    for principal, sub_id in ((ADMIN, 2), (USER, 1)):
        with factory() as db:
            with pytest.raises(RecordNotFound):
                getattr(SubscriptionService(db, principal), method)(sub_id, *args)
    with factory() as db:
        assert SubscriptionService(db, USER).list_visible()["total"] == 0


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("GET", "/1", None),
        ("PUT", "/1", {"notes": "no"}),
        ("DELETE", "/1", None),
        ("POST", "/1/renew", None),
        ("POST", "", BODY),
    ],
)
def test_subscription_http_denies_unowned_client(subscriptions, method, path, body):
    call, _, _ = subscriptions
    assert call(method, "/api/subscriptions" + path, body, user=3)[0] == 404


def test_subscription_client_assignment_and_deleted_parent(subscriptions):
    call, factory, _ = subscriptions
    with factory() as db:
        db.get(Client, 1).assigned_to = 3
        db.commit()
    assert call("GET", "/api/subscriptions/1", user=3)[0] == 200
    with factory() as db:
        service = SubscriptionService(db, USER)
        assert service.list_visible()["total"] == 1
        assert service.renew(1)["renewal_date"] == "2024-03-29T00:00:00Z"
        db.rollback()
    with factory() as db:
        db.get(Client, 1).deleted_at = datetime.utcnow()
        db.commit()
    for user in (1, 3):
        assert call("GET", "/api/subscriptions/1", user=user)[0] == 404
        assert json.loads(call("GET", "/api/subscriptions", user=user)[1])["total"] == 0


def test_subscription_service_rollback_and_calendar_rules(crm):
    _, factory, _ = crm
    with factory() as db:
        service = SubscriptionService(db, ADMIN)
        row = service.create(
            SubscriptionCreateSchema(**BODY, tenant_id=2, created_by=2)
        )
        sub_id = row["id"]
        assert row["renewal_date"] == "2024-02-29T12:00:00Z"
        sub = db.get(Subscription, sub_id)
        assert sub.tenant_id == 1 and sub.created_by == 1
        row = service.update(
            sub_id, SubscriptionUpdateSchema(billing_cycle="yearly", status="cancelled")
        )
        assert row["renewal_date"] == "2025-01-31T12:00:00Z" and row["cancelled_at"]
        cancelled = row["cancelled_at"]
        assert (
            service.update(sub_id, SubscriptionUpdateSchema(status="cancelled"))[
                "cancelled_at"
            ]
            == cancelled
        )
        row = service.renew(sub_id)
        assert (
            row["status"] == "active"
            and row["cancelled_at"] is None
            and row["renewal_date"] == "2026-01-31T12:00:00Z"
        )
        row = service.update(
            sub_id,
            SubscriptionUpdateSchema(
                start_date=datetime(2024, 2, 29), renewal_date=None
            ),
        )
        assert row["renewal_date"] is None
        assert service.renew(sub_id)["renewal_date"] == "2025-02-28T00:00:00Z"
        db.rollback()
    with factory() as db:
        assert db.query(Subscription).count() == 0


@pytest.mark.parametrize(
    "field", ["plan_name", "price", "billing_cycle", "start_date", "status"]
)
def test_subscription_null_required_edit_is_atomic(subscriptions, field):
    call, factory, _ = subscriptions
    assert call("PUT", "/api/subscriptions/1", {field: None, "notes": "no"})[0] == 400
    with factory() as db:
        assert db.get(Subscription, 1).notes is None


@pytest.mark.parametrize(
    "body",
    [
        [],
        {**BODY, "client_id": True},
        {**BODY, "price": float("inf")},
        {**BODY, "start_date": 123},
        {**BODY, "start_date": "bad"},
        {**BODY, "start_date": "9999-12-31"},
    ],
)
def test_subscription_invalid_create(crm, body):
    call, _, _ = crm
    assert call("POST", "/api/subscriptions", body)[0] == 400


def test_subscription_http_lifecycle(crm):
    call, _, _ = crm
    status, body = call(
        "POST",
        "/api/subscriptions",
        {**BODY, "start_date": "2024-01-31T07:00:00-05:00"},
    )
    assert status == 201
    row = json.loads(body)
    path = "/api/subscriptions/" + str(row["id"])
    assert row["start_date"] == "2024-01-31T12:00:00Z"
    assert call("PUT", path, {"status": "paused"})[0] == 200
    assert json.loads(call("GET", "/api/subscriptions?status=paused")[1])["total"] == 1
    assert call("POST", path + "/renew")[0] == 200
    assert call("DELETE", path)[0] == 200
    assert call("GET", path)[0] == 404
    assert call("GET", "/api/subscriptions?client_id=bad")[0] == 400


def test_subscription_malformed_parent_and_foreign_http(subscriptions):
    call, factory, _ = subscriptions
    assert call("GET", "/api/subscriptions/2")[0] == 404
    assert call("POST", "/api/subscriptions", {**BODY, "client_id": 2})[0] == 404
    with factory() as db:
        db.get(Subscription, 1).client_id = 2
        db.commit()
    with factory() as db:
        service = SubscriptionService(db, ADMIN)
        assert service.list_visible()["total"] == 0
        with pytest.raises(RecordNotFound):
            service.detail(1)
