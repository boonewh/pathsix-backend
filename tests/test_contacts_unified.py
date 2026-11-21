"""Every person lives once, in contacts; records keep their contact fields.

Clients and leads expose contact_person/email/phone through their primary
contact, projects point at a contact, converted leads hand their people to the
client, and the directory lists everyone the caller can see, alphabetically.
"""
import pytest

from test_security_boundaries import crm
from app.models import Client, Contact, Lead, Project, split_name
from app.schemas.clients import ClientCreateSchema, ClientUpdateSchema
from app.schemas.contacts import ContactCreateSchema
from app.schemas.leads import LeadCreateSchema
from app.schemas.projects import ProjectCreateSchema, ProjectUpdateSchema
from app.services.clients import ClientService
from app.services.contacts import ContactService
from app.services.leads import LeadService
from app.services.principal import Principal
from app.services.projects import ProjectService
from app.services.purge import PurgeService
from app.services.search import SearchService
from app.utils import auth_utils
from datetime import datetime

ADMIN = Principal(1, 1, frozenset({"admin"}))
ORDINARY = Principal(3, 1, frozenset())


def people(db, **owner):
    key, value = next(iter(owner.items()))
    return db.query(Contact).filter(getattr(Contact, key) == value).order_by(Contact.id).all()


def test_split_name_keeps_multi_word_first_names():
    assert split_name("Mary Ann  Smith") == ("Mary Ann", "Smith")
    assert split_name("Cher") == ("Cher", None)
    assert split_name("  ") == (None, None)


@pytest.mark.parametrize("restricted", [False, True])
def test_client_contact_fields_are_its_primary_contact(crm, restricted):
    _, admin_factory, _ = crm
    factory = auth_utils.SessionLocal if restricted else admin_factory
    with factory() as db:
        service = ClientService(db, ADMIN)
        client_id = service.create(ClientCreateSchema(
            name="ACME Bricks", contact_person="Jane Q Doe", contact_title="Owner",
            email="jane@acme.example.com", phone="555-010-1234", phone_label="mobile"))
        db.commit()
    with factory() as db:
        service = ClientService(db, ADMIN)  # binds the caller for row security
        [jane] = people(db, client_id=client_id)
        assert (jane.is_primary, jane.first_name, jane.last_name, jane.title, jane.email) == \
            (True, "Jane Q", "Doe", "Owner", "jane@acme.example.com")
        detail = ClientService(db, ADMIN).detail(client_id)
        assert (detail["contact_person"], detail["contact_title"], detail["email"], detail["phone_label"]) == \
            ("Jane Q Doe", "Owner", "jane@acme.example.com", "mobile")
        # The primary person is not repeated among the extra contacts.
        assert detail["contacts"] == []
        assert ContactService(db, ADMIN).list_for_parent(client_id=client_id) == []
        ClientService(db, ADMIN).update(client_id, ClientUpdateSchema(email="j.doe@acme.example.com"))
        db.commit()
    with factory() as db:
        search = SearchService(db, ADMIN)
        assert [c.email for c in people(db, client_id=client_id)] == ["j.doe@acme.example.com"]
        # Search and other SQL reads follow the primary contact.
        assert [r["id"] for r in search.search("j.doe@acme")] == [client_id]


def test_clearing_every_person_field_removes_the_primary_contact(crm):
    _, admin_factory, _ = crm
    with admin_factory() as db:
        lead_id = LeadService(db, ADMIN).create(LeadCreateSchema(name="AI Company", contact_person="Sam"))
        db.commit()
        lead = db.get(Lead, lead_id)
        lead.contact_person = None
        db.commit()
        assert people(db, lead_id=lead_id) == []
        # Setting nothing on a record without a person creates no empty contact.
        lead.email = None
        db.commit()
        assert people(db, lead_id=lead_id) == []


def test_conversion_moves_every_lead_person_to_the_client_without_duplicates(crm):
    _, admin_factory, _ = crm
    with admin_factory() as db:
        lead_id = LeadService(db, ADMIN).create(LeadCreateSchema(
            name="AI Company", contact_person="Ada Lovelace", contact_title="CTO",
            email="ada@ai.example.com", secondary_phone="555-010-9999", secondary_phone_label="mobile"))
        ContactService(db, ADMIN).create(ContactCreateSchema(lead_id=lead_id, first_name="Grace", last_name="Hopper"))
        db.commit()
        # The web conversion posts the lead's main person again, minus some details.
        client_id = ClientService(db, ADMIN).create(ClientCreateSchema(
            name="AI Company", contact_person="Ada Lovelace", email="ada@ai.example.com",
            phone="555-010-1111", source_lead_id=lead_id))
        db.commit()
        assert people(db, lead_id=lead_id) == []
        moved = people(db, client_id=client_id)
        assert [(c.display_name, c.is_primary) for c in moved] == [("Ada Lovelace", True), ("Grace Hopper", False)]
        ada = moved[0]
        # Kept from the lead, and the gap (phone) filled from the conversion.
        assert (ada.title, ada.secondary_phone, ada.phone) == ("CTO", "+15550109999", "+15550101111")
        detail = ClientService(db, ADMIN).detail(client_id)
        assert detail["contact_person"] == "Ada Lovelace"
        assert [c["name"] for c in detail["contacts"]] == ["Grace Hopper"]


def test_project_under_a_client_points_at_the_matching_contact(crm):
    _, admin_factory, _ = crm
    with admin_factory() as db:
        client_id = ClientService(db, ADMIN).create(ClientCreateSchema(
            name="ACME", contact_person="Jane Doe", email="jane@acme.example.com"))
        db.commit()
        service = ProjectService(db, ADMIN)
        same = service.create(ProjectCreateSchema(project_name="Same person", client_id=client_id,
                                                  primary_contact_name="JANE DOE", primary_contact_phone="5550102222"))["id"]
        other = service.create(ProjectCreateSchema(project_name="Someone else", client_id=client_id,
                                                   primary_contact_name="Ray Site", primary_contact_email="ray@acme.example.com"))["id"]
        db.commit()
        contacts = people(db, client_id=client_id)
        assert [(c.display_name, c.is_primary) for c in contacts] == [("Jane Doe", True), ("Ray Site", False)]
        assert db.get(Project, same).primary_contact_id == contacts[0].id
        assert contacts[0].phone == "+15550102222"  # filled a gap, kept her email
        assert db.get(Project, other).primary_contact_id == contacts[1].id
        assert service.detail(same)["primary_contact_name"] == "Jane Doe"
        assert service.detail(other)["primary_contact_email"] == "ray@acme.example.com"


def test_standalone_project_owns_its_contact_until_converted(crm):
    _, admin_factory, _ = crm
    with admin_factory() as db:
        service = ProjectService(db, ADMIN)
        project_id = service.create(ProjectCreateSchema(project_name="Warehouse", primary_contact_name="Pat Lee",
                                                        primary_contact_email="pat@example.com"))["id"]
        db.commit()
        [pat] = people(db, project_id=project_id)
        assert pat.is_primary and db.get(Project, project_id).primary_contact_id == pat.id
        # The web project-to-client conversion creates the client with the
        # person, then clears the project's contact fields.
        client_id = ClientService(db, ADMIN).create(ClientCreateSchema(
            name="Warehouse Co", contact_person="Pat Lee", email="pat@example.com"))
        service.update(project_id, ProjectUpdateSchema(
            client_id=client_id, primary_contact_name=None, primary_contact_title=None,
            primary_contact_email=None, primary_contact_phone=None))
        db.commit()
        assert people(db, project_id=project_id) == []
        assert db.get(Project, project_id).primary_contact_id is None
        assert [c.display_name for c in people(db, client_id=client_id)] == ["Pat Lee"]


@pytest.mark.parametrize("restricted", [False, True])
def test_directory_lists_visible_people_alphabetically_with_their_record(crm, restricted):
    _, admin_factory, _ = crm
    with admin_factory() as db:
        db.add_all([
            Client(id=10, tenant_id=1, created_by=3, name="ACME Bricks"),
            Lead(id=10, tenant_id=1, created_by=3, name="AI Company"),
            Lead(id=11, tenant_id=1, created_by=1, name="Admin only"),
            Lead(id=12, tenant_id=1, created_by=3, name="Deleted lead", deleted_at=datetime.utcnow()),
            Project(id=10, tenant_id=1, created_by=3, project_name="Warehouse", project_status="pending"),
        ])
        db.flush()
        db.get(Client, 10).contact_person = "zed Zimmer"
        db.get(Lead, 10).contact_person = "Amy Adams"
        db.get(Lead, 11).contact_person = "Hidden Person"
        db.get(Lead, 12).contact_person = "Gone Person"
        db.add_all([Contact(tenant_id=1, client_id=10, first_name="Bob", last_name="Brown"),
                    Contact(tenant_id=1, client_id=10, email="only@email.example.com")])
        db.get(Project, 10).primary_contact_name = "Carl Cole"
        db.commit()
    factory = auth_utils.SessionLocal if restricted else admin_factory
    with factory() as db:
        result = ContactService(db, ORDINARY).directory()
        rows = [(c["name"], c["parent"]["type"], c["parent"]["name"]) for c in result["contacts"]]
        assert rows == [("Amy Adams", "lead", "AI Company"), ("Bob Brown", "client", "ACME Bricks"),
                        ("Carl Cole", "project", "Warehouse"), ("zed Zimmer", "client", "ACME Bricks"),
                        (None, "client", "ACME Bricks")]
        assert result["total"] == 5
        assert [c["name"] for c in ContactService(db, ORDINARY).directory(query="acme")["contacts"]] == \
            ["Bob Brown", "zed Zimmer", None]
        page = ContactService(db, ORDINARY).directory(page=2, per_page=2)
        assert [c["name"] for c in page["contacts"]] == ["Carl Cole", "zed Zimmer"]
        with pytest.raises(ValueError):
            ContactService(db, ORDINARY).directory(per_page=500)
    with factory() as db:
        admin_names = {c["name"] for c in ContactService(db, ADMIN).directory()["contacts"]}
        assert "Hidden Person" in admin_names and "Gone Person" not in admin_names


def test_contact_api_leaves_primary_and_project_people_to_their_records(crm):
    _, admin_factory, _ = crm
    with admin_factory() as db:
        client_id = ClientService(db, ADMIN).create(ClientCreateSchema(name="ACME", contact_person="Jane Doe"))
        project_id = ProjectService(db, ADMIN).create(ProjectCreateSchema(project_name="Solo", primary_contact_name="Pat"))["id"]
        db.commit()
        for contact in people(db, client_id=client_id) + people(db, project_id=project_id):
            with pytest.raises(Exception) as caught:
                ContactService(db, ADMIN).delete(contact.id)
            assert type(caught.value).__name__ == "RecordNotFound"


def test_purge_takes_the_record_primary_person_but_extra_people_still_block(crm):
    _, admin_factory, _ = crm
    with admin_factory() as db:
        lead_id = LeadService(db, ADMIN).create(LeadCreateSchema(name="Gone", contact_person="Primary Pat"))
        other_id = LeadService(db, ADMIN).create(LeadCreateSchema(name="Kept", contact_person="Kim"))
        db.flush()
        ContactService(db, ADMIN).create(ContactCreateSchema(lead_id=other_id, first_name="Extra"))
        for ident in (lead_id, other_id):
            db.get(Lead, ident).deleted_at = datetime.utcnow()
        db.commit()
        PurgeService(db, ADMIN).purge("leads", [lead_id], single=True)
        db.commit()
        assert db.get(Lead, lead_id) is None and people(db, lead_id=lead_id) == []
        with pytest.raises(Exception) as caught:
            PurgeService(db, ADMIN).purge("leads", [other_id], single=True)
        assert caught.value.blocked[0]["dependencies"] == [{"kind": "contacts", "count": 1}]
