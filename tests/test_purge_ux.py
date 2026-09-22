"""Trash conflicts preserve the entire batch and explain visible dependencies."""
import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError
from purge_fixture import crm as crm

from app.models import Account, Client, Contact, Interaction, Lead, Project, Subscription
from app.services.principal import Principal
from app.services.purge import PurgeConflict, PurgeService, DEPENDENCIES, database_conflict_payload

MODELS = {"clients": Client, "leads": Lead, "projects": Project}


def parents(factory, resource):
    model = MODELS[resource]
    with factory() as db:
        for record_id in (10, 11):
            fields = {"project_name": f"Record {record_id}", "project_status": "pending"} if resource == "projects" else {"name": f"Record {record_id}"}
            db.add(model(id=record_id, tenant_id=1, created_by=1, deleted_at=datetime.utcnow(), **fields))
        db.commit()


@pytest.mark.parametrize("resource", MODELS)
def test_interaction_conflict_is_specific_atomic_and_restorable(crm, resource):
    call, factory, _ = crm
    parents(factory, resource)
    field = resource[:-1] + "_id"
    with factory() as db:
        db.add(Interaction(tenant_id=1, summary="private contents", **{field: 10}))
        # Legacy malformed links must not leak another tenant's record counts.
        if db.bind.dialect.name == "sqlite":
            db.add(Interaction(tenant_id=2, summary="foreign contents", **{field: 10}))
        db.commit()
    for method, path, body in [
        ("DELETE", f"/api/{resource}/10/purge", None),
        ("POST", f"/api/{resource}/bulk-purge", {field + "s": [10, 11]}),
    ]:
        status, raw = call(method, path, body)
        result = json.loads(raw)
        assert status == 409
        assert result["deleted_ids"] == []
        assert result["blocked"] == [{"id": 10, "name": "Record 10", "dependencies": [{"kind": "interactions", "count": 1}]}]
        assert "private contents" not in raw
        with factory() as db:
            assert all(db.get(MODELS[resource], i) is not None for i in (10, 11))
            assert db.query(Interaction).filter(Interaction.tenant_id == 1, getattr(Interaction, field) == 10).count() == 1
    assert call("PUT", f"/api/{resource}/10/restore")[0] == 200


@pytest.mark.parametrize("resource", MODELS)
def test_success_returns_only_eligible_own_ids_and_preserves_active_foreign(crm, resource):
    call, factory, _ = crm
    parents(factory, resource)
    field = resource[:-1] + "_ids"
    with factory() as db:
        db.get(MODELS[resource], 2).deleted_at = datetime.utcnow()
        db.commit()
    status, raw = call("POST", f"/api/{resource}/bulk-purge", {field: [10, 11, 10, 1, 2, 999]})
    assert status == 200
    assert json.loads(raw)["deleted_ids"] == [10, 11]
    with factory() as db:
        assert all(db.get(MODELS[resource], i) is None for i in (10, 11))
        assert all(db.get(MODELS[resource], i) is not None for i in (1, 2))


CASES = [(resource, child, field, label) for resource, dependencies in DEPENDENCIES.items()
         for child, field, label in dependencies]


@pytest.mark.parametrize("resource,child,field,label", CASES)
def test_each_dependency_blocks_even_when_soft_deleted(crm, resource, child, field, label):
    _, factory, _ = crm
    parents(factory, resource)
    fields = {"tenant_id": 1, field: 10}
    if child in (Client, Project):
        fields.update(created_by=1, deleted_at=datetime.utcnow())
    if child is Client:
        fields["name"] = "Converted account"
    elif child is Project:
        fields.update(project_name="Trashed project", project_status="pending")
    elif child is Account:
        fields["account_number"] = "TEST-DEPENDENCY"
    elif child is Subscription:
        fields.update(plan_name="History", price=10, billing_cycle="monthly", start_date=datetime.utcnow(), created_by=1, status="cancelled")
    with factory() as db:
        db.add(child(**fields))
        db.commit()
    with factory() as db:
        with pytest.raises(PurgeConflict) as result:
            PurgeService(db, Principal(1, 1, frozenset({"admin"}))).purge(resource, [10, 11])
        assert result.value.blocked[0]["dependencies"] == [{"kind": label, "count": 1}]
        db.rollback()


@pytest.mark.parametrize("resource", MODELS)
def test_purge_permissions_input_and_caller_rollback(crm, resource):
    call, factory, _ = crm
    parents(factory, resource)
    endpoint = f"/api/{resource}/bulk-purge"
    field = resource[:-1] + "_ids"
    assert call("POST", endpoint, {field: [10]}, user=3)[0] == 403
    for invalid in (None, [], [True], ["10"], [-1]):
        assert call("POST", endpoint, {field: invalid})[0] == 400
    assert call("POST", endpoint, [10])[0] == 400
    assert call("DELETE", f"/api/{resource}/2/purge")[0] == 404
    with factory() as db:
        service = PurgeService(db, Principal(1, 1, frozenset({"admin"})))
        service.purge(resource, [10, 11])
        db.rollback()
    with factory() as db:
        assert all(db.get(MODELS[resource], i) is not None for i in (10, 11))


def test_database_fallback_uses_constraint_not_sensitive_details(crm, monkeypatch):
    call, factory, _ = crm
    parents(factory, "clients")
    class DriverError(Exception):
        pgcode = "23503"
        diag = SimpleNamespace(constraint_name="chat_messages_client_id_fkey")
    exc = IntegrityError("sensitive SQL", {"secret": "hidden"}, DriverError("private contents"))
    def fail(self, *args, **kwargs):
        self.session.query(Client).filter(Client.id == 11).delete()
        raise exc
    monkeypatch.setattr(PurgeService, "purge", fail)
    status, raw = call("POST", "/api/clients/bulk-purge", {"client_ids": [10, 11]})
    assert status == 409
    assert "Linked messages" in json.loads(raw)["error"]
    assert all(secret not in raw for secret in ("sensitive", "hidden", "private contents"))
    assert database_conflict_payload(exc)["blocked"] == []
    with factory() as db:
        assert db.get(Client, 11) is not None


def test_dependency_inventory_covers_every_parent_foreign_key():
    from app.database import Base
    for resource, dependencies in DEPENDENCIES.items():
        actual = {(table.name, column.name) for table in Base.metadata.tables.values()
                  for column in table.columns for fk in column.foreign_keys
                  if fk.column.table.name == resource}
        expected = {(child.__tablename__, field) for child, field, _ in dependencies}
        if resource in ("clients", "leads"):
            expected.add(("chat_messages", resource[:-1] + "_id"))
        assert actual == expected
