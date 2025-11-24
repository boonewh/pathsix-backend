import pytest
from pydantic import ValidationError

from app.schemas.leads import LeadCreateSchema, LeadUpdateSchema
from app.schemas.clients import ClientCreateSchema, ClientUpdateSchema
from app.schemas.contacts import ContactCreateSchema, ContactUpdateSchema
from app.schemas.projects import ProjectCreateSchema, ProjectUpdateSchema
from app.constants import (
    CLIENT_STATUS_OPTIONS,
    LEAD_STATUS_OPTIONS,
    PROJECT_STATUS_OPTIONS,
    TYPE_OPTIONS,
    PHONE_LABELS,
)


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


def test_lead_create_defaults_and_rejects_invalid_status():
    lead = LeadCreateSchema(name="Acme Corp", type=None, phone=None, lead_status=None)

    assert lead.lead_status == "open"
    assert lead.type == "None"
    assert lead.phone is None

    with pytest.raises(ValidationError):
        LeadCreateSchema(name="Acme Corp", lead_status="invalid")


@pytest.mark.parametrize("field", ["phone_label", "secondary_phone_label"])
def test_lead_create_rejects_invalid_phone_labels(field):
    with pytest.raises(ValidationError):
        LeadCreateSchema(name="Acme Corp", **{field: "pager"})


def test_lead_update_validates_type_and_normalizes_phone():
    lead = LeadUpdateSchema(type="Services", phone="   ")

    assert lead.type == "Services"
    assert lead.phone is None

    with pytest.raises(ValidationError):
        LeadUpdateSchema(type="Invalid-Type")


@pytest.mark.parametrize("field", ["phone_label", "secondary_phone_label"])
def test_lead_update_rejects_invalid_phone_labels(field):
    with pytest.raises(ValidationError):
        LeadUpdateSchema(**{field: "pager"})


def test_lead_create_coerces_blank_strings_to_defaults():
    lead = LeadCreateSchema(name="Blank Defaults", type="", lead_status="   ")

    assert lead.type == TYPE_OPTIONS[0]
    assert lead.lead_status == LEAD_STATUS_OPTIONS[0]


def test_client_schema_defaults_and_validations():
    client = ClientCreateSchema(name="Client", type=None, status=None, phone=" 555 ")

    assert client.type == TYPE_OPTIONS[0]
    assert client.status == CLIENT_STATUS_OPTIONS[0]
    assert client.phone == "555"

    with pytest.raises(ValidationError):
        ClientCreateSchema(name="Client", phone_label="invalid")


def test_client_update_normalizes_and_validates_status():
    client_update = ClientUpdateSchema(status=CLIENT_STATUS_OPTIONS[1], phone="   ")

    assert client_update.status == CLIENT_STATUS_OPTIONS[1]
    assert client_update.phone is None

    with pytest.raises(ValidationError):
        ClientUpdateSchema(status="bad")


@pytest.mark.parametrize("field", ["type", "status"])
def test_client_update_rejects_bad_options(field):
    with pytest.raises(ValidationError):
        ClientUpdateSchema(**{field: "invalid"})


def test_contact_schema_validates_phone_labels():
    contact = ContactCreateSchema(phone_label=PHONE_LABELS[1], secondary_phone="   ")
    assert contact.phone_label == PHONE_LABELS[1]
    assert contact.secondary_phone is None

    with pytest.raises(ValidationError):
        ContactCreateSchema(phone_label="invalid")


def test_contact_update_rejects_invalid_phone_label():
    with pytest.raises(ValidationError):
        ContactUpdateSchema(secondary_phone_label="invalid")


def test_contact_create_defaults_phone_and_labels():
    contact = ContactCreateSchema(first_name="Test", phone="   ", secondary_phone_label="")

    assert contact.phone is None
    assert contact.phone_label == PHONE_LABELS[0]
    assert contact.secondary_phone_label is None


def test_project_schema_defaults_and_validations():
    project = ProjectCreateSchema(project_name="Project", project_status=None, type=None, primary_contact_phone=" 999 ")

    assert project.project_status == PROJECT_STATUS_OPTIONS[0]
    assert project.type == TYPE_OPTIONS[0]
    assert project.primary_contact_phone == "999"

    with pytest.raises(ValidationError):
        ProjectCreateSchema(project_name="Project", project_status="bad")


def test_project_update_validates_status_and_phone_label():
    update = ProjectUpdateSchema(project_status=PROJECT_STATUS_OPTIONS[1], primary_contact_phone_label=PHONE_LABELS[2])

    assert update.project_status == PROJECT_STATUS_OPTIONS[1]
    assert update.primary_contact_phone_label == PHONE_LABELS[2]

    with pytest.raises(ValidationError):
        ProjectUpdateSchema(project_status="invalid")


def test_project_update_normalizes_optional_fields():
    update = ProjectUpdateSchema(project_status=" ", primary_contact_phone="   ")

    assert update.project_status is None
    assert update.primary_contact_phone is None
