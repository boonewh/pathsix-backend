"""ORM defaults and archive serialization for the stable UUID column."""
import json
import os
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Account, Client, Contact, Interaction, Lead, Project, Tenant, User
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


def test_project_archive_output_omits_uuid():
    project = Project(id=1, tenant_id=1, created_by=1, uuid=uuid4(),
                      project_name='Project', project_status='pending')
    serialized = project_archive.serialize(project)
    assert 'uuid' not in serialized
    assert serialized['id'] == 1
    json.dumps(serialized)


def test_postgresql_orm_round_trip_and_model_schema_default():
    url = os.getenv('SECURITY_TEST_DATABASE_URL')
    if not url:
        pytest.skip('Requires PostgreSQL')
    schema = 'stable_ids_' + uuid4().hex
    admin = create_engine(url, hide_parameters=True)
    try:
        with admin.begin() as c:
            c.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(url, hide_parameters=True, connect_args={'options': f'-csearch_path={schema}'})
        try:
            Base.metadata.create_all(engine)
            with sessionmaker(bind=engine)() as db:
                db.add(Tenant(id=1, name='T', slug='t'))
                db.add(User(id=1, tenant_id=1, email='u@example.test', password_hash='unused'))
                db.flush()
                client = Client(id=1, tenant_id=1, created_by=1, name='Client')
                db.add(client)
                db.flush()
                records = [client, Lead(tenant_id=1, created_by=1, name='Lead'),
                           Project(tenant_id=1, created_by=1, project_name='P', project_status='pending'),
                           Contact(tenant_id=1, client_id=1, first_name='C'),
                           Interaction(tenant_id=1, client_id=1, summary='I'),
                           Account(tenant_id=1, client_id=1, account_number='A-1')]
                db.add_all(records)
                db.commit()
                expected = {(type(r), r.id): r.uuid for r in records}
                db.expunge_all()
                for (model, record_id), value in expected.items():
                    assert isinstance(value, UUID)
                    assert db.get(model, record_id).uuid == value
            with engine.begin() as c:  # create_all carries the migration's server default
                generated = c.execute(text("INSERT INTO leads (tenant_id, created_by, name) "
                                           "VALUES (1, 1, 'raw') RETURNING uuid")).scalar_one()
                assert generated is not None
        finally:
            engine.dispose()
    finally:
        with admin.begin() as c:
            c.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()
