"""ORM defaults, service attribution and server defaults for change tracking."""
import os
from contextlib import contextmanager
from datetime import datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text, update
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Account, Client, Contact, Interaction, Lead, Project, Tenant, User
from app.schemas.interactions import InteractionCreateSchema
from app.services import project_archive
from app.services.interactions import InteractionService
from app.services.principal import Principal
from app.utils.sales_activity_report import build_report
from test_security_boundaries import crm as crm

MODELS = (Client, Lead, Project, Contact, Interaction, Account)
CHANGED_FIELD = {
    Client: 'name',
    Lead: 'name',
    Project: 'project_name',
    Contact: 'first_name',
    Interaction: 'summary',
    Account: 'account_name',
}
OLD = datetime(2000, 1, 1, 0, 0, 0)


@contextmanager
def _factory(dialect):
    if dialect == 'sqlite':
        engine = create_engine('sqlite://')
        Base.metadata.create_all(engine)
        try:
            yield sessionmaker(bind=engine)
        finally:
            engine.dispose()
        return
    url = os.getenv('SECURITY_TEST_DATABASE_URL')
    if not url:
        pytest.skip('Requires PostgreSQL')
    schema = 'change_tracking_orm_' + uuid4().hex
    admin = create_engine(url, hide_parameters=True)
    try:
        with admin.begin() as c:
            c.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(url, hide_parameters=True, connect_args={'options': f'-csearch_path={schema}'})
        try:
            Base.metadata.create_all(engine)
            yield sessionmaker(bind=engine)
        finally:
            engine.dispose()
    finally:
        with admin.begin() as c:
            c.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()


@pytest.mark.parametrize('dialect', ['sqlite', 'postgresql'])
def test_orm_inserts_and_updates_change_tracking_columns(dialect):
    with _factory(dialect) as factory, factory() as db:
        db.add(Tenant(id=1, name='T', slug='t'))
        db.add(User(id=1, tenant_id=1, email='u@example.test', password_hash='unused'))
        db.flush()
        client = Client(tenant_id=1, created_by=1, name='Client')
        db.add(client)
        db.flush()
        records = [
            client,
            Lead(tenant_id=1, created_by=1, name='Lead'),
            Project(tenant_id=1, created_by=1, project_name='Project', project_status='pending'),
            Contact(tenant_id=1, client_id=client.id, first_name='Contact'),
            Interaction(tenant_id=1, client_id=client.id, summary='Interaction'),
            Account(tenant_id=1, client_id=client.id, account_number='A-1'),
        ]
        db.add_all(records)
        db.commit()
        assert all(record.created_at is not None and record.updated_at is not None
                   for record in records)

        # Core UPDATE bypasses the ORM onupdate, giving a deterministic old value
        # for the subsequent ORM update to move forward.
        for record in records:
            db.execute(update(type(record)).where(type(record).id == record.id)
                       .values(created_at=OLD, updated_at=OLD))
        db.commit()
        db.expire_all()
        for record in records:
            setattr(record, CHANGED_FIELD[type(record)], 'changed')
        db.commit()
        db.expire_all()

        for record in records:
            fresh = db.get(type(record), record.id)
            assert fresh.created_at == OLD
            assert fresh.updated_at > OLD


def test_interaction_service_sets_created_by_from_the_principal(crm):
    _, factory, _ = crm
    principal = Principal(3, 1, frozenset())
    with factory() as db:
        db.get(Client, 1).assigned_to = principal.user_id
        db.commit()
    with factory() as db:
        result = InteractionService(db, principal).create(InteractionCreateSchema(
            contact_date=datetime.utcnow(),
            summary='Created by principal 3',
            client_id=1,
        ))
        db.commit()
        interaction = db.get(Interaction, result['id'])
        assert interaction.tenant_id == principal.tenant_id
        assert interaction.created_by == principal.user_id


def test_interaction_archive_output_omits_sync_only_columns():
    interaction = Interaction(
        id=1, tenant_id=1, client_id=1, summary='Archived call',
        created_by=3, created_at=OLD, updated_at=OLD,
    )
    serialized = project_archive.serialize(interaction)
    for key in ('created_at', 'updated_at', 'created_by', 'uuid'):
        assert key not in serialized
    assert serialized['id'] == 1
    assert serialized['summary'] == 'Archived call'


def test_sales_report_ignores_backfilled_updated_at_without_a_real_edit(crm):
    _, factory, _ = crm
    with factory() as db:
        db.add_all([
            Lead(id=100, tenant_id=1, created_by=1, updated_by=1, name='Backfilled lead',
                 created_at=datetime(2026, 9, 1), updated_at=datetime(2026, 9, 1)),
            Lead(id=101, tenant_id=1, created_by=1, updated_by=1, name='Edited lead',
                 created_at=datetime(2026, 9, 1), updated_at=datetime(2026, 9, 2)),
            # A genuine recorded edit whose created_at was filled in later than it.
            Lead(id=102, tenant_id=1, created_by=1, updated_by=1, name='Edited before backfilled creation',
                 created_at=datetime(2026, 9, 5), updated_at=datetime(2026, 9, 3)),
        ])
        db.commit()
        report = build_report(db, 1, user_id=1)
        edits = [event for event in report['events'] if event['action'] == 'edited']
        assert sorted(event['record_name'] for event in edits) == [
            'Edited before backfilled creation', 'Edited lead']
        summary = next(user for user in report['users'] if user['user_id'] == 1)
        assert summary['edits'] == 2


def test_postgresql_raw_insert_uses_server_change_tracking_defaults():
    url = os.getenv('SECURITY_TEST_DATABASE_URL')
    if not url:
        pytest.skip('Requires PostgreSQL')
    schema = 'change_tracking_' + uuid4().hex
    admin = create_engine(url, hide_parameters=True)
    try:
        with admin.begin() as c:
            c.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(url, hide_parameters=True,
                               connect_args={'options': f'-csearch_path={schema}'})
        try:
            Base.metadata.create_all(engine)
            with sessionmaker(bind=engine)() as db:
                db.add(Tenant(id=1, name='T', slug='t'))
                db.commit()
            with engine.begin() as c:
                created, updated = c.execute(text(
                    "INSERT INTO interactions (tenant_id, summary, followup_status) "
                    "VALUES (1, 'raw', 'pending') "
                    "RETURNING created_at, updated_at")).one()
                transaction_now = c.execute(text("SELECT timezone('utc', now())")).scalar_one()
            assert created == transaction_now
            assert updated == transaction_now
        finally:
            engine.dispose()
    finally:
        with admin.begin() as c:
            c.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()
