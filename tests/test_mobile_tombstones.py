"""Tombstones: soft deletes stay as hidden rows until purge or retention removes them.

Covered here (brief F-3):
- account numbers unique among live rows only, on a model-built schema;
- service deletes become tombstones with attribution and an audit entry;
- tombstones invisible to services, routes, relationships, client stats, reports,
  search, the AI connector reads, the sales activity report and project archive;
- opt-in reads with include_tombstones=True;
- purge removes tombstoned children but live children still block;
- retention dry run/apply, target checks, the bypass-RLS refusal, and
  concurrent delete/edit guards that wait on a real row lock.

The migration tier covers the same account-number rule on the migrated schema.
"""
import io
import json
import os
import queue
import sys
import threading
import time
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
from app.schemas.accounts import AccountUpdateSchema
from app.schemas.contacts import ContactUpdateSchema
from app.schemas.interactions import InteractionUpdateSchema
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
# A same-tenant ordinary user who is not the fixture owner (user 1).
ACTOR = Principal(3, 1, frozenset())
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


def _wait_for_ungranted_lock(engine, pid, timeout=5.0):
    """Wait (bounded) until PostgreSQL shows this backend waiting for a lock."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with engine.connect() as monitor:
            waiting = monitor.execute(text(
                'SELECT count(*) FROM pg_locks WHERE pid=:pid AND NOT granted'),
                {'pid': pid}).scalar_one()
        if waiting:
            return
        time.sleep(0.02)
    raise AssertionError(f'backend {pid} did not wait for a row lock within {timeout}s')


def _race_with_uncommitted_tombstone(factory, model, field, action):
    """Hold a tombstone open in one transaction and run `action` on another.

    The action must reach the write guard and wait on the row lock, not pass on a
    stale read. B commits after the wait is observed; the action must then fail
    with RecordNotFound. Returns (error, value before B, B's deleted_at).
    """
    engine = factory.kw['bind']
    with factory() as first, factory() as second:
        before = getattr(_raw(second, model.__tablename__, 1, field), field)
        other = second.query(model).filter(model.id == 1).one()
        bury(other, 3)
        second.flush()
        b_deleted_at = other.deleted_at

        ready, outcome, pid = queue.Queue(), {}, None

        def worker():
            try:
                pid_local = first.connection().execute(
                    text('SELECT pg_backend_pid()')).scalar_one()
                ready.put(pid_local)
                # The service loads the still-live row before B commits, then
                # reaches the guard and blocks on B's row lock.
                action(first)
            except Exception as exc:  # noqa: BLE001 - reported to the test
                outcome['error'] = exc

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        try:
            try:
                pid = ready.get(timeout=5)
            except queue.Empty:
                raise AssertionError('the writer did not start within 5s')
            _wait_for_ungranted_lock(engine, pid)
        finally:
            second.commit()
            thread.join(timeout=10)
            if thread.is_alive() and pid is not None:
                # Release a writer stuck on a regression so the suite fails, not hangs.
                with engine.begin() as c:
                    c.execute(text('SELECT pg_terminate_backend(:pid)'), {'pid': pid})
                thread.join(timeout=5)
        assert not thread.is_alive(), 'the writer did not finish after the competing delete committed'
        first.rollback()
    return outcome.get('error'), before, b_deleted_at


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
    # Client 1 belongs to user 1; hand it to ordinary user 3 so attribution is
    # proved with an actor who differs from the fixture owner.
    with factory() as db:
        db.query(Client).filter(Client.id == 1).one().assigned_to = ACTOR.user_id
        db.commit()

    with factory() as db:
        ContactService(db, ACTOR).delete(1)
        AccountService(db, ACTOR).delete(1)
        InteractionService(db, ACTOR).delete(1)
        db.commit()

    with factory() as db:
        for model, entity in ((Contact, 'contact'), (Account, 'account'),
                              (Interaction, 'interaction')):
            deleted_at, deleted_by = db.execute(text(
                f'SELECT deleted_at, deleted_by FROM {model.__tablename__} WHERE id=1')).one()
            assert deleted_at is not None and deleted_by == ACTOR.user_id
            logs = db.query(ActivityLog).filter(
                ActivityLog.entity_type == entity,
                ActivityLog.entity_id == 1,
                ActivityLog.action == ActivityType.deleted,
            ).all()
            assert len(logs) == 1, entity
            assert logs[0].user_id == ACTOR.user_id, entity

        for service, record_id in ((ContactService(db, ACTOR), 1),
                                   (AccountService(db, ACTOR), 1),
                                   (InteractionService(db, ACTOR), 1)):
            with pytest.raises(RecordNotFound):
                service.delete(record_id)

        with pytest.raises(RecordNotFound):
            AccountService(db, ACTOR).detail(1)
        with pytest.raises(RecordNotFound):
            InteractionService(db, ACTOR).complete(1)
        assert ContactService(db, ACTOR).list_for_parent(client_id=1) == []
        assert AccountService(db, ACTOR).list_visible() == []
        assert ClientService(db, ACTOR).detail(1)['contacts'] == []

        # The deleted contact is not readable through the connector either.
        with pytest.raises(RecordNotFound):
            dispatch(db, ACTOR, 'unused', 'get_contact', {'contact_id': 1})

    # The same failure through a request/principal context, including the
    # contact read and the interaction calendar read.
    assert call('DELETE', '/api/accounts/1')[0] == 404
    assert call('GET', '/api/accounts/1')[0] == 404
    assert call('GET', '/api/accounts')[0] == 200
    assert call('DELETE', '/api/contacts/1')[0] == 404
    assert call('GET', '/api/contacts?client_id=1')[0] == 200
    assert call('DELETE', '/api/interactions/1')[0] == 404
    assert call('PUT', '/api/interactions/1/complete')[0] == 404
    assert call('GET', '/api/interactions?client_id=1')[0] == 200
    assert call('GET', '/api/interactions/1/calendar.ics')[0] == 404


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
        db.add(Client(id=30, tenant_id=1, created_by=1, name='Inactive client'))
        db.add_all([
            Interaction(id=20, tenant_id=1, client_id=1, summary='Recent tombstone',
                        contact_date=now, follow_up=now - timedelta(days=5),
                        followup_status=FollowUpStatus.pending,
                        deleted_at=now, deleted_by=1),
            Interaction(id=21, tenant_id=1, client_id=1, summary='Future tombstone',
                        contact_date=now, follow_up=now + timedelta(days=5),
                        followup_status=FollowUpStatus.pending,
                        deleted_at=now, deleted_by=1),
            Interaction(id=22, tenant_id=1, client_id=1, summary='Live overdue',
                        contact_date=now - timedelta(days=3),
                        follow_up=now - timedelta(days=5),
                        followup_status=FollowUpStatus.pending),
            Interaction(id=23, tenant_id=1, client_id=1, summary='Live upcoming',
                        contact_date=now - timedelta(days=1),
                        follow_up=now + timedelta(days=5),
                        followup_status=FollowUpStatus.pending),
            Interaction(id=24, tenant_id=1, client_id=30, summary='Client 30 tombstone',
                        contact_date=now - timedelta(days=1),
                        deleted_at=now, deleted_by=1),
        ])
        db.commit()

    with factory() as db:
        stats = ClientService(db, ADMIN).list_mine(page=1, per_page=20)
        row = next(item for item in stats['clients'] if item['id'] == 1)
        # The two live interactions count; the two tombstones do not.
        assert row['interaction_count'] == 2
        assert row['last_interaction_date'] is not None
        assert row['last_interaction_date'][:10] == \
            (now - timedelta(days=1)).date().isoformat()

        reports = ReportService(db, ADMIN)
        follow_up = reports.follow_up_report()
        overdue_ids = {item['interaction_id'] for item in follow_up['overdue_follow_ups']}
        assert 22 in overdue_ids and 20 not in overdue_ids
        inactive = {item['client_id']: item for item in follow_up['inactive_clients']}
        # Client 1 is active through live interactions; client 30 has only a tombstone.
        assert 1 not in inactive
        assert inactive[30]['last_interaction'] is None

        upcoming = reports.upcoming_tasks_report()
        upcoming_ids = {item['interaction_id'] for item in upcoming['upcoming_tasks']}
        assert 23 in upcoming_ids and 21 not in upcoming_ids
        assert reports.client_retention_report()['active_with_recent_interactions'] == 1


# ---------------------------------------------------------------------------
# A4: search and AI connector reads hide tombstones.
# ---------------------------------------------------------------------------
def test_search_and_ai_reads_hide_tombstones(crm):
    _, factory, _ = crm
    with factory() as db:
        db.add_all([
            Account(id=20, tenant_id=1, client_id=1, account_number='ZZ-TOMB',
                    account_name='Unique Tombstone Account', deleted_at=_now(), deleted_by=1),
            Account(id=21, tenant_id=1, client_id=1, account_number='ZZ-LIVE',
                    account_name='Unique Live Account'),
            Contact(id=20, tenant_id=1, client_id=1, first_name='Unique',
                    last_name='Tombstone', deleted_at=_now(), deleted_by=1),
            Contact(id=21, tenant_id=1, client_id=1, first_name='Unique', last_name='Live'),
            Interaction(id=20, tenant_id=1, client_id=1,
                        summary='Unique tombstone interaction', deleted_at=_now(), deleted_by=1),
            Interaction(id=21, tenant_id=1, client_id=1, summary='Unique live interaction'),
        ])
        db.commit()

    with factory() as db:
        # Search only indexes accounts among these three entities.
        found = SearchService(db, ADMIN).search('Unique')
        account_ids = {item['id'] for item in found if item['type'] == 'account'}
        assert 21 in account_ids and 20 not in account_ids
        reads = RecordReads(db, ADMIN)
        contact_ids = {item['id'] for item in reads.contacts(client_id=1)['contacts']}
        assert 21 in contact_ids and 20 not in contact_ids
        interaction_ids = {item['id'] for item in reads.interactions(client_id=1)['interactions']}
        assert 21 in interaction_ids and 20 not in interaction_ids
        with pytest.raises(RecordNotFound):
            reads.contacts(contact_id=20, limit=1)
        assert dispatch(db, ADMIN, 'unused', 'get_contact',
                        {'contact_id': 21})['contact']['id'] == 21
        with pytest.raises(RecordNotFound):
            dispatch(db, ADMIN, 'unused', 'get_contact', {'contact_id': 20})
        listed = {item['id'] for item in dispatch(
            db, ADMIN, 'unused', 'list_contacts', {'client_id': 1})['contacts']}
        assert 21 in listed and 20 not in listed
        listed = {item['id'] for item in dispatch(
            db, ADMIN, 'unused', 'list_interactions', {'client_id': 1})['interactions']}
        assert 21 in listed and 20 not in listed


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
            Interaction(id=22, tenant_id=1, lead_id=1, summary='Live lead interaction'),
            Interaction(id=23, tenant_id=1, client_id=1, summary='Live pending interaction',
                        followup_status=FollowUpStatus.pending),
        ])
        db.commit()

    with factory() as db:
        # Only the live lead interaction moves; the tombstone stays put.
        assert InteractionService(db, ADMIN).transfer(1, 1)['transferred'] == 1
        InteractionService(db, ADMIN).complete(23)
        with pytest.raises(RecordNotFound):
            InteractionService(db, ADMIN).complete(21)
        db.commit()

    with factory() as db:
        lead_id, client_id = _raw(db, 'interactions', 20, 'lead_id, client_id')
        assert lead_id == 1 and client_id is None
        lead_id, client_id = _raw(db, 'interactions', 22, 'lead_id, client_id')
        assert lead_id is None and client_id == 1
        status = _raw(db, 'interactions', 21, 'followup_status').followup_status
        assert status == 'pending'
        status = _raw(db, 'interactions', 23, 'followup_status').followup_status
        assert status == 'completed'


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
        for client_id, child_id in ((10, 30), (11, 31)):
            _trashed_parent(db, 'clients', client_id)
            db.add_all([
                Contact(id=child_id, tenant_id=1, client_id=client_id, first_name='Child',
                        deleted_at=_now(), deleted_by=1),
                Account(id=child_id, tenant_id=1, client_id=client_id,
                        account_number=f'PURGE-{child_id}',
                        deleted_at=_now(), deleted_by=1),
                Interaction(id=child_id, tenant_id=1, client_id=client_id, summary='Child',
                            deleted_at=_now(), deleted_by=1),
            ])
        db.commit()
        result = PurgeService(db, ADMIN).purge('clients', [10])
        assert result['deleted_ids'] == [10]
        db.commit()

    with factory() as db:
        for table in ('clients', 'contacts', 'accounts', 'interactions'):
            assert _count(db, table, 10 if table == 'clients' else 30) == 0
        # A tombstoned child of an unselected parent is not collateral.
        assert _count(db, 'clients', 11) == 1
        for table in ('contacts', 'accounts', 'interactions'):
            assert _count(db, table, 31) == 1, table


@pytest.mark.parametrize('resource,child,field,label', [
    ('leads', Contact, 'lead_id', 'contacts'),
    ('leads', Interaction, 'lead_id', 'interactions'),
    ('projects', Interaction, 'project_id', 'interactions'),
])
def test_purge_removes_tombstoned_children_with_a_lead_or_project(crm, resource, child,
                                                                  field, label):
    _, factory, _ = crm
    with factory() as db:
        for parent_id, child_id in ((10, 30), (11, 31)):
            _trashed_parent(db, resource, parent_id)
            fields = {'id': child_id, 'tenant_id': 1, field: parent_id,
                      'deleted_at': _now(), 'deleted_by': 1}
            fields['first_name' if child is Contact else 'summary'] = 'Child'
            db.add(child(**fields))
        db.commit()
        result = PurgeService(db, ADMIN).purge(resource, [10])
        assert result['deleted_ids'] == [10]
        db.commit()

    with factory() as db:
        assert _count(db, resource, 10) == 0
        assert _count(db, child.__tablename__, 30) == 0
        # The unselected parent and its tombstoned child survive.
        assert _count(db, resource, 11) == 1
        assert _count(db, child.__tablename__, 31) == 1


@pytest.mark.parametrize('resource,child,field,label', [
    ('clients', Contact, 'client_id', 'contacts'),
    ('leads', Contact, 'lead_id', 'contacts'),
    ('leads', Interaction, 'lead_id', 'interactions'),
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
        # The blocked purge leaves both tombstones and the live child in place.
        for record_id in (30, 31, 32):
            assert _count(db, child.__tablename__, record_id) == 1, record_id


# ---------------------------------------------------------------------------
# A7: retention dry run/apply, and target checks.
# ---------------------------------------------------------------------------
def test_prune_dry_run_and_apply_keep_young_tombstones_and_live_rows(crm):
    _, factory, _ = crm
    if not _is_postgresql(factory):
        pytest.skip('Requires PostgreSQL')
    now = _now()
    cutoff = now - timedelta(days=90)
    # Deleted just before the cutoff, exactly at it, just after it, and never.
    # The strict `<` in prune means only record 32 is old enough.
    deleted_at_by_id = {
        30: cutoff + timedelta(minutes=1),
        31: cutoff,
        32: cutoff - timedelta(minutes=1),
        33: None,
    }
    with factory() as db:
        for record_id, deleted_at in deleted_at_by_id.items():
            db.add(Contact(id=record_id, tenant_id=1, client_id=1, first_name='Contact',
                           deleted_at=deleted_at, deleted_by=1 if deleted_at else None))
            db.add(Interaction(id=record_id, tenant_id=1, client_id=1, summary='Interaction',
                               deleted_at=deleted_at, deleted_by=1 if deleted_at else None))
            db.add(Account(id=record_id, tenant_id=1, client_id=1,
                           account_number=f'PRUNE-{record_id}',
                           deleted_at=deleted_at, deleted_by=1 if deleted_at else None))
        db.commit()

        counts = prune_tombstones.prune(db.connection(), now, apply=False)
        assert counts == {'interactions': 1, 'contacts': 1, 'accounts': 1, 'sync_purges': 0,
                          'write_receipts': 0}
        db.commit()
        # A dry run deletes nothing in any of the three tables.
        for table in TOMBSTONE_TABLES:
            for record_id in deleted_at_by_id:
                assert _count(db, table, record_id) == 1, (table, record_id)

        applied = prune_tombstones.prune(db.connection(), now, apply=True)
        assert applied == counts
        db.commit()

    with factory() as db:
        for table in TOMBSTONE_TABLES:
            for record_id in (30, 31, 33):
                assert _count(db, table, record_id) == 1, (table, record_id)
            assert _count(db, table, 32) == 0, table


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
    old = _now() - timedelta(days=91)
    with factory() as db:
        for record_id in (30, 31):
            db.add(Contact(id=record_id, tenant_id=1, client_id=1, first_name='Old',
                           deleted_at=old, deleted_by=1))
            db.add(Interaction(id=record_id, tenant_id=1, client_id=1, summary='Old',
                               deleted_at=old, deleted_by=1))
            db.add(Account(id=record_id, tenant_id=1, client_id=1,
                           account_number=f'OLD-{record_id}',
                           deleted_at=old, deleted_by=1))
        db.commit()

    with engine.begin() as c:
        c.execute(text(f'CREATE ROLE {quote(role)} LOGIN PASSWORD \'test-only\''))
    calls = []
    try:
        admin_url = URL.create('postgresql', username=role, password='test-only',
                               host=url.host, port=url.port, database=url.database)
        monkeypatch.setenv('FLY_APP_NAME', 'pathsixsolutions-backend-mobile')
        monkeypatch.setenv('DATABASE_URL', str(url))
        monkeypatch.setattr(sys, 'argv', ['prune_tombstones.py', '--apply'])
        monkeypatch.setattr(prune_tombstones, 'prune',
                            lambda *args, **kwargs: calls.append((args, kwargs)) or {})
        # str(URL) masks the password; the script needs the real one to log in.
        monkeypatch.setattr(sys, 'stdin', io.StringIO(
            admin_url.render_as_string(hide_password=False) + '\n'))
        # --apply must still refuse before prune() runs.
        with pytest.raises(SystemExit, match='bypass row security'):
            prune_tombstones.main()
        assert calls == []
    finally:
        with engine.begin() as c:
            c.execute(text('SELECT pg_terminate_backend(pid) FROM pg_stat_activity '
                           'WHERE usename=:role AND pid <> pg_backend_pid()'), {'role': role})
            c.execute(text(f'DROP ROLE IF EXISTS {quote(role)}'))

    with factory() as db:
        for table in TOMBSTONE_TABLES:
            for record_id in (30, 31):
                assert _count(db, table, record_id) == 1, (table, record_id)


# ---------------------------------------------------------------------------
# Concurrent writes (F-3 review follow-ups).
# ---------------------------------------------------------------------------
@pytest.mark.parametrize('model,field,service,schema,payload', [
    (Contact, 'first_name', ContactService, ContactUpdateSchema, {'first_name': 'raced'}),
    (Account, 'account_number', AccountService, AccountUpdateSchema,
     {'account_number': 'raced'}),
    (Interaction, 'summary', InteractionService, InteractionUpdateSchema, {'summary': 'raced'}),
])
def test_service_edit_waits_for_a_concurrent_tombstone_then_is_not_found(
        crm, model, field, service, schema, payload):
    """A writer must reach the guard and wait on B's row lock, not pass on a stale read."""
    _, factory, _ = crm
    if not _is_postgresql(factory):
        pytest.skip('Requires PostgreSQL')
    error, before, b_deleted_at = _race_with_uncommitted_tombstone(
        factory, model, field,
        lambda session: service(session, ADMIN).update(1, schema(**payload)))
    assert isinstance(error, RecordNotFound), repr(error)
    with factory() as db:
        row = _raw(db, model.__tablename__, 1, f'{field}, deleted_at, deleted_by')
        assert getattr(row, field) == before
        assert row.deleted_at is not None and row.deleted_by == 3
        assert row.deleted_at == b_deleted_at
        logs = db.query(ActivityLog).filter(
            ActivityLog.entity_type == model.__name__.lower(),
            ActivityLog.entity_id == 1,
            ActivityLog.action.in_((ActivityType.edited, ActivityType.deleted)),
        ).all()
        assert logs == []


@pytest.mark.parametrize('model,service', [
    (Contact, ContactService),
    (Account, AccountService),
    (Interaction, InteractionService),
])
def test_service_double_delete_waits_for_a_concurrent_tombstone_then_is_not_found(
        crm, model, service):
    _, factory, _ = crm
    if not _is_postgresql(factory):
        pytest.skip('Requires PostgreSQL')
    error, _, b_deleted_at = _race_with_uncommitted_tombstone(
        factory, model, 'deleted_at',
        lambda session: service(session, ADMIN).delete(1))
    assert isinstance(error, RecordNotFound), repr(error)
    with factory() as db:
        row = _raw(db, model.__tablename__, 1, 'deleted_at, deleted_by')
        assert row.deleted_at is not None and row.deleted_by == 3
        assert row.deleted_at == b_deleted_at
        logs = db.query(ActivityLog).filter(
            ActivityLog.entity_type == model.__name__.lower(),
            ActivityLog.entity_id == 1,
            ActivityLog.action.in_((ActivityType.edited, ActivityType.deleted)),
        ).all()
        assert logs == []


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
        assert counts == {'interactions': 0, 'contacts': 1, 'accounts': 0, 'sync_purges': 0,
                          'write_receipts': 0}
        db.commit()
        locked.rollback()

    with factory() as db:
        assert _count(db, 'contacts', 30) == 1
        assert _count(db, 'contacts', 31) == 0
