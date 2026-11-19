"""ORM defaults and archive serialization for the stable UUID column."""
import json
from uuid import UUID, uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Account, Client, Contact, Interaction, Lead, Project
from app.services import project_archive


def _session():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def test_orm_created_records_get_their_own_uuid():
    with _session()() as db:
        client = Client(tenant_id=1, created_by=1, name='Client')
        lead = Lead(tenant_id=1, created_by=1, name='Lead')
        project = Project(tenant_id=1, created_by=1, project_name='Project', project_status='pending')
        contact = Contact(tenant_id=1, client_id=1, first_name='Contact')
        interaction = Interaction(tenant_id=1, client_id=1, summary='Interaction')
        account = Account(tenant_id=1, client_id=1, account_number='A-1')
        records = (client, lead, project, contact, interaction, account)
        db.add_all(records)
        db.commit()

        values = [record.uuid for record in records]
        assert all(isinstance(value, UUID) for value in values)
        assert len({str(value) for value in values}) == len(values)
        for record in records:
            assert db.get(type(record), record.id).uuid == record.uuid


def test_project_archive_serializes_uuid_as_a_json_string():
    project = Project(id=1, tenant_id=1, created_by=1, uuid=uuid4(),
                      project_name='Project', project_status='pending')
    serialized = project_archive.serialize(project)
    assert serialized['uuid'] == str(project.uuid)
    assert isinstance(serialized['uuid'], str)
    assert json.loads(json.dumps(serialized))['uuid'] == str(project.uuid)
