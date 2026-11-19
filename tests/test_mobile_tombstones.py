"""Tombstones: soft deletes stay as hidden rows until purge or retention removes them.

Covered here (brief F-3):
- account numbers unique among live rows only, on a model-built schema;
- service deletes become tombstones with attribution and an audit entry;
- tombstones invisible to services, routes, relationships, client stats, reports,
  search, the AI connector reads, the sales activity report and project archive;
- opt-in reads with include_tombstones=True;
- purge removes tombstoned children but live children still block;
- retention dry run/apply, target checks, the bypass-RLS refusal, and the
  concurrent delete/edit guards.

The migration tier covers the same account-number rule on the migrated schema.
"""
import io
import json
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import (
    Account,
    ActivityLog,
    ActivityType,
    Client,
    Contact,
    FollowUpStatus,
    Interaction,
    Lead,
    Project,
    Tenant,
    User,
)
from app.services.accounts import AccountService
from app.services.clients import ClientService
from app.services.contacts import ContactService
from app.services.errors import RecordNotFound
from app.services.interactions import InteractionService
from app.services.leads import LeadService
from app.services.mcp_reads import dispatch
from app.services.mcp_records import RecordReads
from app.services.principal import Principal
from app.services.purge import PurgeConflict, PurgeService
from app.services.reports import ReportService
from app.services.search import SearchService
from app.utils.sales_activity_report import build_report
from app.utils.tombstones import INCLUDE, bury
from purge_fixture import crm as crm
from test_project_archive import archive, preview_and_archive

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prune_tombstones  # noqa: E402

ADMIN = Principal(1, 1, frozenset({'admin'}))
TOMBSTONE_TABLES = ('interactions', 'contacts', 'accounts')


@contextmanager
def _factory(dialect):
    """A model-built schema, in memory for SQLite or a throwaway PostgreSQL schema."""
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
    schema = 'tombstone_orm_' + uuid4().hex
    admin = create_engine(url, hide_parameters=True)
    try:
        with admin.begin() as c:
            c.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(url, hide_parameters=True,
                               connect_args={'options': f'-csearch_path={schema}'})
        try:
            Base.metadata.create_all(engine)
            yield sessionmaker(bind=engine)
        finally:
            engine.dispose()
    finally:
        with admin.begin() as c:
            c.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()


def _is_postgresql(factory):
    return factory.kw['bind'].dialect.name == 'postgresql'


def _now():
    return datetime.utcnow()


def _raw(db, table, record_id, columns='*'):
    return db.execute(text(f'SELECT {columns} FROM {table} WHERE id=:id'),
                      {'id': record_id}).one()


def _count(db, table, record_id):
    return db.execute(text(f'SELECT count(*) FROM {table} WHERE id=:id'),
                      {'id': record_id}).scalar_one()


def _trashed_parent(db, resource, record_id=10):
    fields = {'tenant_id': 1, 'created_by': 1, 'deleted_at': _now(), 'deleted_by': 1}
    if resource == 'clients':
        db.add(Client(id=record_id, name=f'Trashed client {record_id}', **fields))
    elif resource == 'leads':
        db.add(Lead(id=record_id, name=f'Trashed lead {record_id}', **fields))
    else:
        db.add(Project(id=record_id, project_name=f'Trashed project {record_id}',
                       project_status='pending', **fields))


# ---------------------------------------------------------------------------
# A2: account numbers are unique among live rows only.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize('dialect', ['sqlite', 'postgresql'])
def test_account_number_unique_among_live_accounts_only(dialect):
    with _factory(dialect) as factory, factory() as db:
        db.add(Tenant(id=1, name='T', slug='t'))
        db.add(User(id=1, tenant_id=1, email='u@example.test', password_hash='unused'))
        db.flush()
        db.add(Client(id=1, tenant_id=1, created_by=1, name='Client'))
        db.flush()
        db.add(Account(id=1, tenant_id=1, client_id=1, account_number='A-1'))
        db.commit()

        db.add(Account(id=2, tenant_id=1, client_id=1, account_number='A-1'))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        # The same number is free once its only holder is a tombstone.
        db.execute(text('UPDATE accounts SET deleted_at=:at, deleted_by=1 WHERE id=1'),
                   {'at': _now()})
        db.commit()
        db.add(Account(id=2, tenant_id=1, client_id=1, account_number='A-1'))
        db.commit()

        # And the live row that reused it is still protected.
        db.add(Account(id=3, tenant_id=1, client_id=1, account_number='A-1'))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


# ---------------------------------------------------------------------------
# A3: service deletes become tombstones, with attribution and an audit entry.
# ---------------------------------------------------------------------------
def test_service_deletes_become_attributed_tombstones_and_second_access_fails(crm):
    call, factory, _ = crm
    with factory() as db:
        ContactService(db, ADMIN).delete(1)
        AccountService(db, ADMIN).delete(1)
        InteractionService(db, ADMIN).delete(1)
        db.commit()

    with factory() as db:
        for model, entity in ((Contact, 'contact'), (Account, 'account'),
                              (Interaction, 'interaction')):
            deleted_at, deleted_by = db.execute(text(
                f'SELECT deleted_at, deleted_by FROM {model.__tablename__} WHERE id=1')).one()
            assert deleted_at is not None and deleted_by == ADMIN.user_id
            logs = db.query(ActivityLog).filter(
                ActivityLog.entity_type == entity,
                ActivityLog.entity_id == 1,
                ActivityLog.action == ActivityType.deleted,
            ).all()
            assert len(logs) == 1, entity

        for service, record_id in ((ContactService(db, ADMIN), 1),
                                   (AccountService(db, ADMIN), 1),
                                   (InteractionService(db, ADMIN), 1)):
            with pytest.raises(RecordNotFound):
                service.delete(record_id)

        with pytest.raises(RecordNotFound):
            AccountService(db, ADMIN).detail(1)
        with pytest.raises(RecordNotFound):
            InteractionService(db, ADMIN).complete(1)
        assert ContactService(db, ADMIN).list_for_parent(client_id=1) == []
        assert AccountService(db, ADMIN).list_visible() == []
        assert ClientService(db, ADMIN).detail(1)['contacts'] == []

    # The same failure through a request/principal context.
    assert call('DELETE', '/api/accounts/1')[0] == 404
    assert call('GET', '/api/accounts/1')[0] == 404
    assert call('GET', '/api/accounts')[0] == 200
    assert call('DELETE', '/api/contacts/1')[0] == 404
    assert call('GET', '/api/contacts?client_id=1')[0] == 200
    assert call('DELETE', '/api/interactions/1')[0] == 404
    assert call('PUT', '/api/interactions/1/complete')[0] == 404
    assert call('GET', '/api/interactions?client_id=1')[0] == 200


# ---------------------------------------------------------------------------
# A4: invisible through services and relationship loads.
# ---------------------------------------------------------------------------
def test_tombstones_are_invisible_to_services_and_relationships(crm):
    _, factory, _ = crm
    with factory() as db:
        for model, record_id in ((Contact, 1), (Account, 1), (Interaction, 1)):
            row = db.query(model).filter(model.id == record_id).one()
            row.deleted_at, row.deleted_by = _now(), 1
        db.add_all([
            Contact(id=20, tenant_id=1, lead_id=1, first_name='Lead tombstone',
                    deleted_at=_now(), deleted_by=1),
            Interaction(id=20, tenant_id=1, project_id=1, summary='Project tombstone',
                        deleted_at=_now(), deleted_by=1),
            Interaction(id=21, tenant_id=1, client_id=1, summary='Client tombstone',
                        contact_date=_now(), deleted_at=_now(), deleted_by=1),
            Account(id=20, tenant_id=1, client_id=1, account_number='A-TOMB',
                    deleted_at=_now(), deleted_by=1),
        ])
        db.commit()

    with factory() as db:
        assert ContactService(db, ADMIN).list_for_parent(client_id=1) == []
        assert ContactService(db, ADMIN).list_for_parent(lead_id=1) == []
        assert AccountService(db, ADMIN).list_visible() == []
        with pytest.raises(RecordNotFound):
            AccountService(db, ADMIN).detail(1)
        assert InteractionService(db, ADMIN).list_visible(client_id=1)['interactions'] == []
        with pytest.raises(RecordNotFound):
            InteractionService(db, ADMIN).delete(1)
        with pytest.raises(RecordNotFound):
            InteractionService(db, ADMIN).complete(1)

        assert ClientService(db, ADMIN).detail(1)['contacts'] == []
        assert LeadService(db, ADMIN).detail(1)['contacts'] == []

        client = db.query(Client).filter(Client.id == 1).one()
        lead = db.query(Lead).filter(Lead.id == 1).one()
        project = db.query(Project).filter(Project.id == 1).one()
        assert client.contacts == []
        assert client.interactions == []
        assert client.accounts == []
        assert lead.contacts == []
        assert project.interactions == []


# ---------------------------------------------------------------------------
# A4: client stats and report aggregates ignore tombstoned interactions.
# ---------------------------------------------------------------------------
def test_client_stats_and_reports_ignore_tombstoned_interactions(crm):
    _, factory, _ = crm
    now = _now()
    with factory() as db:
        live = db.query(Interaction).filter(Interaction.id == 1).one()
        live.deleted_at, live.deleted_by = now, 1
        db.add_all([
            Interaction(id=20, tenant_id=1, client_id=1, summary='Recent tombstone',
                        contact_date=now, follow_up=now - timedelta(days=5),
                        followup_status=FollowUpStatus.pending,
                        deleted_at=now, deleted_by=1),
            Interaction(id=21, tenant_id=1, client_id=1, summary='Future tombstone',
                        contact_date=now, follow_up=now + timedelta(days=5),
                        followup_status=FollowUpStatus.pending,
                        deleted_at=now, deleted_by=1),
        ])
        db.commit()

    with factory() as db:
        stats = ClientService(db, ADMIN).list_mine(page=1, per_page=20)
        row = next(item for item in stats['clients'] if item['id'] == 1)
        assert row['interaction_count'] == 0
        assert row['last_interaction_date'] is None

        reports = ReportService(db, ADMIN)
        follow_up = reports.follow_up_report()
        assert all(item['interaction_id'] != 20 for item in follow_up['overdue_follow_ups'])
        inactive = next(item for item in follow_up['inactive_clients']
                        if item['client_id'] == 1)
        assert inactive['last_interaction'] is None

        upcoming = reports.upcoming_tasks_report()
        assert all(item['interaction_id'] != 21 for item in upcoming['upcoming_tasks'])
        assert reports.client_retention_report()['active_with_recent_interactions'] == 0


# ---------------------------------------------------------------------------
# A4: search and AI connector reads hide tombstones.
# ---------------------------------------------------------------------------
def test_search_and_ai_reads_hide_tombstones(crm):
    _, factory, _ = crm
    with factory() as db:
        db.add_all([
            Account(id=20, tenant_id=1, client_id=1, account_number='ZZ-TOMB',
                    account_name='Unique Tombstone Account', deleted_at=_now(), deleted_by=1),
            Contact(id=20, tenant_id=1, client_id=1, first_name='Unique',
                    last_name='Tombstone', deleted_at=_now(), deleted_by=1),
            Interaction(id=20, tenant_id=1, client_id=1,
                        summary='Unique tombstone interaction', deleted_at=_now(), deleted_by=1),
        ])
        db.commit()

    with factory() as db:
        assert SearchService(db, ADMIN).search('Unique Tombstone') == []
        reads = RecordReads(db, ADMIN)
        assert 20 not in {item['id'] for item in reads.contacts(client_id=1)['contacts']}
        assert 20 not in {item['id'] for item in reads.interactions(client_id=1)['interactions']}
        with pytest.raises(RecordNotFound):
            reads.contacts(contact_id=20, limit=1)
        with pytest.raises(RecordNotFound):
            dispatch(db, ADMIN, 'unused', 'get_contact', {'contact_id': 20})
        assert 20 not in {item['id'] for item in dispatch(
            db, ADMIN, 'unused', 'list_contacts', {'client_id': 1})['contacts']}
        assert 20 not in {item['id'] for item in dispatch(
            db, ADMIN, 'unused', 'list_interactions', {'client_id': 1})['interactions']}


# ---------------------------------------------------------------------------
# A4: the sales activity report must not read a tombstone as a live interaction.
# ---------------------------------------------------------------------------
def test_sales_activity_report_does_not_read_tombstoned_interactions(crm):
    _, factory, _ = crm
    with factory() as db:
        # Inserted without an audit entry, so the report's legacy-interaction
        # branch would otherwise surface this row.
        db.add(Interaction(id=20, tenant_id=1, client_id=1, summary='Legacy tombstone',
                           contact_date=_now(), deleted_at=_now(), deleted_by=1))
        db.commit()

    with factory() as db:
        report = build_report(db, 1)
        # The report may announce the deletion from its own tombstone (historical
        # audit), but it must not read the row as a live interaction.
        assert all(not (event['entity_type'] == 'interaction' and event['entity_id'] == 20
                        and event['action'] == 'created')
                   for event in report['events'])


# ---------------------------------------------------------------------------
# A4: project archive detail hides tombstones and omits their sync-only columns.
# ---------------------------------------------------------------------------
def test_project_archive_detail_hides_tombstoned_interactions(archive):
    call, factory, _ = archive
    with factory() as db:
        db.add(Interaction(id=20, tenant_id=1, project_id=1, summary='Archived tombstone',
                           deleted_at=_now(), deleted_by=1))
        db.commit()
    preview_and_archive(call, user=2)
    status, body = call('GET', '/api/owner/project-archive/1', user=4)
    assert status == 200
    result = json.loads(body)
    ids = [item['id'] for item in result['interactions']]
    assert 10 in ids and 20 not in ids
    live = next(item for item in result['interactions'] if item['id'] == 10)
    assert 'deleted_at' not in live and 'deleted_by' not in live


# ---------------------------------------------------------------------------
# A4: bulk ORM updates leave tombstones untouched.
# ---------------------------------------------------------------------------
def test_bulk_transfer_and_complete_leave_tombstones_untouched(crm):
    _, factory, _ = crm
    with factory() as db:
        db.add_all([
            Interaction(id=20, tenant_id=1, lead_id=1, summary='Lead tombstone',
                        deleted_at=_now(), deleted_by=1),
            Interaction(id=21, tenant_id=1, client_id=1, summary='Complete tombstone',
                        followup_status=FollowUpStatus.pending,
                        deleted_at=_now(), deleted_by=1),
        ])
        db.commit()

    with factory() as db:
        assert InteractionService(db, ADMIN).transfer(1, 1)['transferred'] == 0
        with pytest.raises(RecordNotFound):
            InteractionService(db, ADMIN).complete(21)
        db.commit()

    with factory() as db:
        lead_id, client_id = _raw(db, 'interactions', 20, 'lead_id, client_id')
        assert lead_id == 1 and client_id is None
        status = _raw(db, 'interactions', 21, 'followup_status').followup_status
        assert status == 'pending'


# ---------------------------------------------------------------------------
# A5: include_tombstones=True opts a statement back in.
# ---------------------------------------------------------------------------
def test_include_tombstones_returns_the_hidden_row(crm):
    _, factory, _ = crm
    with factory() as db:
        contact = db.query(Contact).filter(Contact.id == 1).one()
        contact.deleted_at, contact.deleted_by = _now(), 1
        db.commit()

    with factory() as db:
        assert db.query(Contact).filter(Contact.id == 1).first() is None
        assert db.query(Contact).execution_options(**{INCLUDE: True}).filter(
            Contact.id == 1).one().id == 1
        row = db.execute(select(Contact).where(Contact.id == 1),
                         execution_options={INCLUDE: True}).scalar_one()
        assert row.id == 1
        assert db.execute(select(Contact).where(Contact.id == 1)).first() is None


# ---------------------------------------------------------------------------
# A6: purge removes tombstoned children with their parent.
# ---------------------------------------------------------------------------
def test_purge_removes_tombstoned_children_with_the_client(crm):
    _, factory, _ = crm
    with factory() as db:
        _trashed_parent(db, 'clients', 10)
        db.add_all([
            Contact(id=30, tenant_id=1, client_id=10, first_name='Child',
                    deleted_at=_now(), deleted_by=1),
            Account(id=30, tenant_id=1, client_id=10, account_number='PURGE-30',
                    deleted_at=_now(), deleted_by=1),
            Interaction(id=30, tenant_id=1, client_id=10, summary='Child',
                        deleted_at=_now(), deleted_by=1),
        ])
        db.commit()
        result = PurgeService(db, ADMIN).purge('clients', [10])
        assert result['deleted_ids'] == [10]
        db.commit()

    with factory() as db:
        for table in ('clients', 'contacts', 'accounts', 'interactions'):
            assert _count(db, table, 10 if table == 'clients' else 30) == 0


@pytest.mark.parametrize('resource,child,field,label', [
    ('leads', Contact, 'lead_id', 'contacts'),
    ('projects', Interaction, 'project_id', 'interactions'),
])
def test_purge_removes_tombstoned_children_with_a_lead_or_project(crm, resource, child,
                                                                  field, label):
    _, factory, _ = crm
    with factory() as db:
        _trashed_parent(db, resource, 10)
        fields = {'tenant_id': 1, field: 10, 'deleted_at': _now(), 'deleted_by': 1}
        if child is Contact:
            fields['first_name'] = 'Child'
        else:
            fields['summary'] = 'Child'
        db.add(child(id=30, **fields))
        db.commit()
        result = PurgeService(db, ADMIN).purge(resource, [10])
        assert result['deleted_ids'] == [10]
        db.commit()

    with factory() as db:
        assert _count(db, resource, 10) == 0
        assert _count(db, child.__tablename__, 30) == 0


@pytest.mark.parametrize('resource,child,field,label', [
    ('clients', Contact, 'client_id', 'contacts'),
    ('leads', Contact, 'lead_id', 'contacts'),
    ('projects', Interaction, 'project_id', 'interactions'),
])
def test_live_child_blocks_purge_and_the_count_excludes_tombstones(
        crm, resource, child, field, label):
    _, factory, _ = crm
    with factory() as db:
        _trashed_parent(db, resource, 10)
        for record_id in (30, 31):
            fields = {'id': record_id, 'tenant_id': 1, field: 10,
                      'deleted_at': _now(), 'deleted_by': 1}
            fields['first_name' if child is Contact else 'summary'] = 'Tombstone'
            db.add(child(**fields))
        live = {'id': 32, 'tenant_id': 1, field: 10}
        live['first_name' if child is Contact else 'summary'] = 'Live'
        db.add(child(**live))
        db.commit()

        with pytest.raises(PurgeConflict) as blocked:
            PurgeService(db, ADMIN).purge(resource, [10])
        assert blocked.value.blocked[0]['dependencies'] == [{'kind': label, 'count': 1}]
        db.rollback()

    with factory() as db:
        assert _count(db, resource, 10) == 1
        assert _count(db, child.__tablename__, 32) == 1


# ---------------------------------------------------------------------------
# A7: retention dry run/apply, and target checks.
# ---------------------------------------------------------------------------
def test_prune_dry_run_and_apply_keep_young_tombstones_and_live_rows(crm):
    _, factory, _ = crm
    if not _is_postgresql(factory):
        pytest.skip('Requires PostgreSQL')
    now = _now()
    old, young = now - timedelta(days=91), now - timedelta(days=10)
    with factory() as db:
        for record_id, deleted_at in ((30, old), (31, young), (32, None)):
            db.add(Contact(id=record_id, tenant_id=1, client_id=1, first_name='Contact',
                           deleted_at=deleted_at, deleted_by=1 if deleted_at else None))
            db.add(Interaction(id=record_id, tenant_id=1, client_id=1, summary='Interaction',
                               deleted_at=deleted_at, deleted_by=1 if deleted_at else None))
            db.add(Account(id=record_id, tenant_id=1, client_id=1,
                           account_number=f'PRUNE-{record_id}',
                           deleted_at=deleted_at, deleted_by=1 if deleted_at else None))
        db.commit()

        counts = prune_tombstones.prune(db.connection(), now, apply=False)
        assert counts == {'interactions': 1, 'contacts': 1, 'accounts': 1}
        db.commit()
        assert _count(db, 'contacts', 30) == 1

        applied = prune_tombstones.prune(db.connection(), now, apply=True)
        assert applied == counts
        db.commit()

    with factory() as db:
        for table in TOMBSTONE_TABLES:
            assert _count(db, table, 30) == 0, table
            assert _count(db, table, 31) == 1, table
            assert _count(db, table, 32) == 1, table


def test_prune_target_is_mobile_only_same_database_and_without_overrides(monkeypatch):
    monkeypatch.setenv('FLY_APP_NAME', 'pathsixsolutions-backend-mobile')
    monkeypatch.setenv('DATABASE_URL', 'postgres://runtime:x@db.internal:5432/app')
    assert prune_tombstones.require_target(
        'postgresql://admin:x@db.internal:5432/app').username == 'admin'
    assert prune_tombstones.require_target(
        'postgresql://admin:x@db.internal:5432/app?sslmode=require').username == 'admin'

    for app in ('pathsixsolutions-backend-staging', 'pathsixsolutions-backend',
                'pathsixsolutions-backend-other', ''):
        monkeypatch.setenv('FLY_APP_NAME', app)
        with pytest.raises(SystemExit):
            prune_tombstones.require_target('postgresql://admin:x@db.internal:5432/app')

    monkeypatch.setenv('FLY_APP_NAME', 'pathsixsolutions-backend-mobile')
    for url in ('postgresql://admin:x@other.internal:5432/app',
                'postgresql://admin:x@db.internal:5433/app',
                'postgresql://admin:x@db.internal:5432/other',
                'postgresql://admin:x@db.internal:5432/app?host=elsewhere',
                'postgresql://admin:x@db.internal:5432/app?port=5433',
                'postgresql://admin:x@db.internal:5432/app?dbname=other'):
        with pytest.raises(SystemExit):
            prune_tombstones.require_target(url)


def test_prune_main_refuses_a_role_that_cannot_bypass_row_security(crm, monkeypatch):
    _, factory, _ = crm
    if not _is_postgresql(factory):
        pytest.skip('Requires PostgreSQL')
    engine = factory.kw['bind']
    url = engine.url
    role = 'prune_no_bypass_' + uuid4().hex[:12]
    quote = engine.dialect.identifier_preparer.quote
    with engine.begin() as c:
        c.execute(text(f'CREATE ROLE {quote(role)} LOGIN PASSWORD \'test-only\''))
    try:
        admin_url = URL.create('postgresql', username=role, password='test-only',
                               host=url.host, port=url.port, database=url.database)
        monkeypatch.setenv('FLY_APP_NAME', 'pathsixsolutions-backend-mobile')
        monkeypatch.setenv('DATABASE_URL', str(url))
        monkeypatch.setattr(sys, 'argv', ['prune_tombstones.py'])
        # str(URL) masks the password; the script needs the real one to log in.
        monkeypatch.setattr(sys, 'stdin', io.StringIO(
            admin_url.render_as_string(hide_password=False) + '\n'))
        with pytest.raises(SystemExit, match='bypass row security'):
            prune_tombstones.main()
    finally:
        with engine.begin() as c:
            c.execute(text('SELECT pg_terminate_backend(pid) FROM pg_stat_activity '
                           'WHERE usename=:role AND pid <> pg_backend_pid()'), {'role': role})
            c.execute(text(f'DROP ROLE IF EXISTS {quote(role)}'))


# ---------------------------------------------------------------------------
# Concurrent writes (F-3 review follow-ups).
# ---------------------------------------------------------------------------
@pytest.mark.parametrize('model,field,original', [
    (Contact, 'first_name', 'A'),
    (Interaction, 'summary', 'A'),
])
def test_stale_edit_after_a_concurrent_tombstone_is_not_found(crm, model, field, original):
    _, factory, _ = crm
    if not _is_postgresql(factory):
        pytest.skip('Requires PostgreSQL')
    with factory() as first, factory() as second:
        record = first.query(model).filter(model.id == 1).one()
        other = second.query(model).filter(model.id == 1).one()
        other.deleted_at, other.deleted_by = _now(), 3
        second.commit()

        setattr(record, field, 'stale')
        with pytest.raises(RecordNotFound):
            first.flush()
        first.rollback()

    with factory() as db:
        row = _raw(db, model.__tablename__, 1, f'{field}, deleted_at, deleted_by')
        assert getattr(row, field) == original
        assert row.deleted_at is not None and row.deleted_by == 3


def test_stale_edit_after_a_hard_delete_is_not_found_not_stale_data(crm):
    _, factory, _ = crm
    if not _is_postgresql(factory):
        pytest.skip('Requires PostgreSQL')
    with factory() as first, factory() as second:
        contact = first.query(Contact).filter(Contact.id == 1).one()
        second.execute(text('DELETE FROM contacts WHERE id=1'))
        second.commit()

        contact.first_name = 'stale'
        with pytest.raises(RecordNotFound):
            first.flush()
        first.rollback()

    with factory() as db:
        assert _count(db, 'contacts', 1) == 0


@pytest.mark.parametrize('model,service', [
    (Contact, ContactService),
    (Interaction, InteractionService),
])
def test_second_concurrent_delete_is_not_found_and_keeps_the_first_user(crm, model, service):
    _, factory, _ = crm
    if not _is_postgresql(factory):
        pytest.skip('Requires PostgreSQL')
    with factory() as first, factory() as second:
        loaded = first.query(model).filter(model.id == 1).one()
        other = second.query(model).filter(model.id == 1).one()
        other.deleted_at, other.deleted_by = _now(), 3
        second.commit()

        with pytest.raises(RecordNotFound):
            service(first, ADMIN).delete(1)

        # A direct write through an object loaded before the race is guarded too.
        bury(loaded, 1)
        with pytest.raises(RecordNotFound):
            first.flush()
        first.rollback()

    with factory() as db:
        assert _raw(db, model.__tablename__, 1, 'deleted_by').deleted_by == 3


def test_prune_apply_skips_a_tombstone_locked_by_another_transaction(crm):
    _, factory, _ = crm
    if not _is_postgresql(factory):
        pytest.skip('Requires PostgreSQL')
    now = _now()
    old = now - timedelta(days=91)
    with factory() as db:
        for record_id in (30, 31):
            db.add(Contact(id=record_id, tenant_id=1, client_id=1, first_name='Contact',
                           deleted_at=old, deleted_by=1))
        db.commit()

    with factory() as locked, factory() as db:
        locked.execute(text('SELECT id FROM contacts WHERE id=30 FOR UPDATE')).scalar_one()
        # A regression that waits on the locked row fails in one second instead of hanging.
        db.execute(text("SET LOCAL lock_timeout='1s'"))
        counts = prune_tombstones.prune(db.connection(), now, apply=True)
        assert counts == {'interactions': 0, 'contacts': 1, 'accounts': 0}
        db.commit()
        locked.rollback()

    with factory() as db:
        assert _count(db, 'contacts', 30) == 1
        assert _count(db, 'contacts', 31) == 0
