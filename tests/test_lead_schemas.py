import pytest
from pydantic import ValidationError

from app.schemas.leads import LeadCreateSchema, LeadUpdateSchema


def test_lead_create_accepts_valid_options_and_normalizes_phone():
    lead = LeadCreateSchema(
        name="Acme Corp",
        lead_status="qualified",
        phone_label="mobile",
        secondary_phone_label="home",
        type="Retail",
        phone=" 123-456-7890 "
    )

    assert lead.lead_status == "qualified"
    assert lead.phone == "123-456-7890"
    assert lead.phone_label == "mobile"
    assert lead.secondary_phone_label == "home"
    assert lead.type == "Retail"


def test_lead_create_defaults_and_accepts_tenant_defined_status():
    lead = LeadCreateSchema(name="Acme Corp", type=None, phone=None, lead_status=None)

    assert lead.lead_status is None
    assert lead.type == "None"
    assert lead.phone is None

    custom = LeadCreateSchema(name="Acme Corp", lead_status="  awaiting-review  ")
    assert custom.lead_status == "awaiting-review"


@pytest.mark.parametrize("field", ["phone_label", "secondary_phone_label"])
def test_lead_create_rejects_invalid_phone_labels(field):
    with pytest.raises(ValidationError):
        LeadCreateSchema(name="Acme Corp", **{field: "pager"})


def test_lead_update_accepts_tenant_defined_type_and_normalizes_phone():
    lead = LeadUpdateSchema(type="Services", phone="   ")

    assert lead.type == "Services"
    assert lead.phone is None

    custom = LeadUpdateSchema(type="  Industry Specific  ")
    assert custom.type == "Industry Specific"


@pytest.mark.parametrize("field", ["phone_label", "secondary_phone_label"])
def test_lead_update_rejects_invalid_phone_labels(field):
    with pytest.raises(ValidationError):
        LeadUpdateSchema(**{field: "pager"})
