import json
from datetime import datetime
import pytest
from app.models import Account, Client, ActivityLog
from app.schemas.accounts import AccountCreateSchema, AccountUpdateSchema
from app.services.accounts import AccountService
from app.services.principal import Principal
from app.services.errors import RecordNotFound
from test_security_boundaries import crm as crm

ADMIN = Principal(1, 1, frozenset({"admin"}))
USER = Principal(3, 1, frozenset())


@pytest.mark.parametrize(
    "method,args",
    [
        ("detail", []),
        ("record_view", []),
        ("delete", []),
        ("update", [AccountUpdateSchema(notes="no")]),
    ],
)
def test_account_service_denies_foreign_and_unowned(crm, method, args):
    _, factory, _ = crm
    for principal in (USER, Principal(2, 2, frozenset({"admin"}))):
        with factory() as db:
            with pytest.raises(RecordNotFound):
                getattr(AccountService(db, principal), method)(1, *args)
            assert AccountService(db, principal).list_visible() == []


def test_account_service_parent_move_and_rollback(crm):
    _, factory, _ = crm
    with factory() as db:
        db.add(Client(id=3, tenant_id=1, created_by=3, name="Destination"))
        db.commit()
    with factory() as db:
        service = AccountService(db, ADMIN)
        retained = db.get(Account, 1)
        assert service.detail(1)["client_name"] == "Private client 1"
        assert db.query(ActivityLog).count() == 0
        with pytest.raises(RecordNotFound):
            service.update(1, AccountUpdateSchema(client_id=2, notes="forbidden"))
        assert retained.notes is None
        service.update(1, AccountUpdateSchema(client_id=3))
        assert service.detail(1)["client_name"] == "Destination"
        assert retained.client.name == "Destination"
        service.record_view(1)
        assert db.query(ActivityLog).count() == 1
        db.rollback()
    with factory() as db:
        assert db.get(Account, 1).client_id == 1
        assert db.query(ActivityLog).count() == 0


def test_account_service_requires_current_and_destination_client(crm):
    _, factory, _ = crm
    with factory() as db:
        db.add(Client(id=3, tenant_id=1, created_by=3, name="Owned"))
        db.commit()
    with factory() as db:
        service = AccountService(db, USER)
        with pytest.raises(RecordNotFound):
            service.update(1, AccountUpdateSchema(client_id=3))
        created = service.create(
            AccountCreateSchema(client_id=3, account_number="own", tenant_id=2)
        )
        assert db.get(Account, created["id"]).tenant_id == 1
        with pytest.raises(RecordNotFound):
            service.update(created["id"], AccountUpdateSchema(client_id=1))
        service.delete(created["id"])
        db.rollback()


def test_account_service_denies_deleted_and_malformed_parents(crm):
    _, factory, _ = crm
    with factory() as db:
        db.get(Client, 1).deleted_at = datetime.utcnow()
        db.commit()
    with factory() as db:
        service = AccountService(db, ADMIN)
        assert service.list_visible() == []
        with pytest.raises(RecordNotFound):
            service.detail(1)
    with factory() as db:
        db.get(Account, 1).client_id = 2
        db.commit()
    with factory() as db:
        service = AccountService(db, ADMIN)
        assert service.list_visible() == []
        with pytest.raises(RecordNotFound):
            service.detail(1)


@pytest.mark.parametrize(
    "body",
    [
        [],
        {"client_id": True, "account_number": "A"},
        {"client_id": 1, "account_number": " "},
        {"client_id": 1, "account_number": "A", "opened_on": "bad"},
        {"client_id": 1, "account_number": "A", "opened_on": 123},
    ],
)
def test_account_http_invalid_create_is_safe(crm, body):
    call, _, _ = crm
    assert call("POST", "/api/accounts", body)[0] == 400


@pytest.mark.parametrize(
    "body",
    [
        {"client_id": None},
        {"account_number": None},
        {"opened_on": "bad", "notes": "must rollback"},
    ],
)
def test_account_http_invalid_update_is_atomic(crm, body):
    call, factory, _ = crm
    assert call("PUT", "/api/accounts/1", body)[0] == 400
    with factory() as db:
        account = db.get(Account, 1)
        assert (
            account.client_id == 1
            and account.account_number == "A"
            and account.notes is None
        )


def test_account_http_lifecycle(crm):
    call, _, _ = crm
    status, body = call(
        "POST",
        "/api/accounts",
        {
            "client_id": 1,
            "account_number": "new",
            "status": "unknown",
            "opened_on": "2026-09-12T13:00:00-05:00",
        },
    )
    assert status == 201
    row = json.loads(body)
    assert row["status"] == "active" and row["opened_on"] == "2026-09-12T18:00:00Z"
    path = "/api/accounts/" + str(row["id"])
    assert call("GET", path)[0] == 200
    status, body = call("PUT", path, {"status": "closed", "opened_on": ""})
    assert status == 200 and json.loads(body)["opened_on"] == row["opened_on"]
    assert call("DELETE", path)[0] == 200
    assert call("GET", path)[0] == 404
