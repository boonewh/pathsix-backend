import json
import pytest
from purge_fixture import crm
from app.models import Lead, Tenant
from app.utils.lead_options import normalize_lead_options

CONFIG = {"leads": {"statuses": ["prospecting", "qualified", "won", "lost"]},
          "businessTypes": ["None", "Oil & Gas"]}


@pytest.mark.parametrize("values, expected", [
    ({}, {"lead_status": "prospecting", "type": "None"}),
    ({"lead_status": "  ", "type": None}, {"lead_status": "prospecting", "type": "None"}),
    ({"lead_status": " WON ", "type": " oil & gas "}, {"lead_status": "won", "type": "Oil & Gas"}),
    ({"lead_status": "Custom", "type": "Other industry"}, {"lead_status": "Custom", "type": "Other industry"}),
])
def test_create_and_import_option_policy(values, expected):
    assert normalize_lead_options(values, CONFIG, creating=True) == expected


def configure(factory):
    with factory() as db:
        db.get(Tenant, 1).config = CONFIG
        db.get(Tenant, 2).config = {"leads": {"statuses": ["intake"]}}
        db.commit()


def test_create_uses_own_tenant_default_and_normalizes_type(crm):
    call, factory, _ = crm
    configure(factory)
    for user, status in [(1, "prospecting"), (2, "intake")]:
        code, body = call("POST", "/api/leads/", {"name": "Example", "type": " oil & gas "}, user=user)
        assert code == 201, body
        with factory() as db:
            lead = db.get(Lead, json.loads(body)["id"])
            assert lead.lead_status == status
            assert lead.type == ("Oil & Gas" if user == 1 else "oil & gas")


@pytest.mark.parametrize("status, business_type", [(None, None), ("", ""), ("Custom", " oil & gas ")])
def test_note_only_update_preserves_existing_options(crm, status, business_type):
    call, factory, _ = crm
    configure(factory)
    with factory() as db:
        lead = db.get(Lead, 1)
        lead.lead_status, lead.type = status, business_type
        db.commit()
    code, body = call("PUT", "/api/leads/1", {"notes": "Updated"})
    assert code == 200, body
    with factory() as db:
        lead = db.get(Lead, 1)
        assert (lead.lead_status, lead.type, lead.notes) == (status, business_type, "Updated")


def test_explicit_option_update_normalizes_and_records_won_date(crm):
    call, factory, _ = crm
    configure(factory)
    code, body = call("PUT", "/api/leads/1", {"lead_status": " WON ", "type": " oil & gas "})
    assert code == 200, body
    with factory() as db:
        lead = db.get(Lead, 1)
        assert (lead.lead_status, lead.type) == ("won", "Oil & Gas")
        assert lead.converted_on is not None
    assert call("PUT", "/api/leads/1", {"type": "Retail"}, user=2)[0] == 404


def test_csv_import_uses_tenant_options(crm, monkeypatch):
    import asyncio
    import io
    import time
    from authlib.jose import jwt
    from werkzeug.datastructures import FileStorage
    from app.routes import imports

    _, factory, app = crm
    configure(factory)

    async def no_email(**kwargs):
        pass
    monkeypatch.setattr(imports, "send_email", no_email)
    token = jwt.encode({"alg": "HS256"}, {"sub": 1, "exp": int(time.time()) + 300}, app.config["SECRET_KEY"]).decode()

    async def run():
        response = await app.test_client().post(
            "/api/import/leads/submit",
            headers={"Authorization": f"Bearer {token}"},
            form={"assigned_user_email": "a@example.test", "column_mappings": json.dumps([
                {"csvColumn": field, "leadField": field} for field in ("name", "type", "lead_status")
            ])},
            files={"file": FileStorage(stream=io.BytesIO(
                b"name,type,lead_status\nImported default,,\nImported choices, oil & gas , QUALIFIED \n"
            ), filename="leads.csv", content_type="text/csv")},
        )
        return response.status_code, await response.get_json()

    code, body = asyncio.run(run())
    assert code == 200, body
    assert body["successful_imports"] == 2
    assert body["failed_imports"] == 0
    with factory() as db:
        rows = db.query(Lead).filter(Lead.name.like("Imported%"), Lead.tenant_id == 1).all()
        assert {row.name: (row.type, row.lead_status) for row in rows} == {
            "Imported default": ("None", "prospecting"),
            "Imported choices": ("Oil & Gas", "qualified"),
        }
