"""Archive isolation through real HTTP routes, aggregates, and write paths."""
import json
from datetime import datetime

import pytest
from sqlalchemy import select, func
from sqlalchemy.orm import aliased
from purge_fixture import crm as crm
from app.models import Project, Interaction, Client, Lead, ActivityLog, ActivityType, User
from app.routes import project_archive
from app.utils.project_archive_access import visible_activity, visible_interaction
from app.utils.sales_activity_report import build_report


@pytest.fixture
def archive(crm, monkeypatch):
    call, factory, app = crm
    monkeypatch.setenv('PROJECT_ARCHIVE_OWNER_IDS', '2,4')
    # Staging's auth factory uses the restricted runtime database role.
    from app.utils import auth_utils
    monkeypatch.setattr(project_archive, 'SessionLocal', auth_utils.SessionLocal)
    # Owner 2 belongs to tenant 2; owner 4 is the ASFI administrator.
    with factory() as db:
        db.add(User(id=4, tenant_id=1, email='owner@example.test', password_hash='unused'))
        p = db.get(Project, 1)
        p.created_at = datetime(2026, 4, 30, 23, 59, 59)
        p.project_name = 'Private archive sentinel'
        p.project_worth = 12345
        p.project_status = 'completed'
        p.client_id = 1
        p.updated_at = datetime(2026, 5, 2)
        db.add(Project(id=10, tenant_id=1, created_by=1, project_name='Trashed sentinel',
            project_status='lost', created_at=datetime(2026, 3, 1), deleted_at=datetime(2026, 4, 1)))
        db.add(Project(id=11, tenant_id=1, created_by=1, project_name='May boundary',
            project_status='active', created_at=datetime(2026, 5, 1)))
        db.add(Interaction(id=10, tenant_id=1, project_id=1, summary='Secret conversation',
            follow_up=datetime(2026, 10, 2)))
        db.add(ActivityLog(tenant_id=1, user_id=1, entity_type='project', entity_id=1,
            action=ActivityType.viewed, description='Private archive sentinel'))
        db.add(ActivityLog(tenant_id=1, user_id=1, entity_type='interaction', entity_id=10,
            action=ActivityType.created, description='Secret conversation'))
        db.commit()
    return call, factory, app


def preview_and_archive(call, user=4):
    status, body = call('GET', '/api/owner/project-archive/preview', user=user)
    assert status == 200, body
    preview = json.loads(body)
    assert preview['total'] == 2 and preview['in_trash'] == 1
    status, body = call('POST', '/api/owner/project-archive/archive', {
        'preview_token': preview['preview_token'], 'confirmation': 'ARCHIVE 2'}, user=user)
    assert status == 200, body
    return preview


def test_owner_scope_auth_and_empty_preview_are_read_only(archive, monkeypatch):
    call, factory, _ = archive
    for user in (0, 1, 3):
        for method, path in [('GET', ''), ('GET', '/preview'), ('GET', '/1'),
                             ('POST', '/archive'), ('POST', '/1/restore')]:
            status, body = call(method, '/api/owner/project-archive'+path, {}, user=user)
            assert status in (401, 403), body
            assert 'sentinel' not in body
    for owner in (2, 4):
        status, body = call('GET', '/api/owner/project-archive/preview', user=owner)
        assert status == 200, body
        assert [p['id'] for p in json.loads(body)['projects']] == [1, 10]
    assert call('GET', '/api/owner/project-archive', user=4, claims={'exp': 1})[0] == 401
    with factory() as db:
        assert db.query(Project).count() == 4
    monkeypatch.delenv('PROJECT_ARCHIVE_OWNER_IDS')
    assert call('GET', '/api/owner/project-archive', user=4)[0] == 403


def test_archive_hides_every_normal_surface_and_preserves_data(archive):
    call, factory, _ = archive
    with factory() as db:
        before = {m.__tablename__: [tuple(getattr(r, c.name) for c in m.__table__.columns) for r in db.query(m).order_by(m.id)] for m in (Client, Lead)}
    preview_and_archive(call, user=2)
    for path in ['/api/projects/', '/api/projects/all', '/api/projects/trash',
                 '/api/projects/by-client/1', '/api/projects/by-lead/1',
                 '/api/search/?q=sentinel', '/api/interactions/', '/api/interactions/all',
                 '/api/activity/recent', '/api/reports/pipeline', '/api/reports/revenue-by-client',
                 '/api/reports/sales-activity', '/api/reports/project-performance',
                 '/api/reports/revenue-forecast', '/api/reports/follow-ups']:
        status, body = call('GET', path, user=1)
        assert status == 200, (path, status, body)
        assert all(text not in body for text in ('sentinel', 'Secret conversation', '12345')), (path, body)
    for method, path, data in [
        ('GET', '/api/projects/1', None), ('PUT', '/api/projects/1', {'project_name': 'stolen'}),
        ('DELETE', '/api/projects/1', None), ('PUT', '/api/projects/1/assign', {'assigned_to': 1}),
        ('PUT', '/api/projects/10/restore', None), ('DELETE', '/api/projects/10/purge', None),
        ('PUT', '/api/interactions/10', {'summary': 'stolen'}),
        ('DELETE', '/api/interactions/10', None), ('PUT', '/api/interactions/10/complete', None),
        ('GET', '/api/interactions/10/calendar.ics', None),
    ]:
        status, body = call(method, path, data, user=1)
        assert status == 404, (path, status, body)
    with factory() as db:
        assert db.query(Project).count() == 2
        assert db.query(Interaction).count() == 2
        assert db.query(func.sum(Project.project_worth)).scalar() in (None, 0)
        alias = aliased(Project)
        assert len(db.query(alias).all()) == 2
        # Aggregate report statements use nested UNION/CTEs rather than root ORM queries.
        report = build_report(db, 1)
        assert 'sentinel' not in json.dumps(report)
        after = {m.__tablename__: [tuple(getattr(r, c.name) for c in m.__table__.columns) for r in db.query(m).order_by(m.id)] for m in (Client, Lead)}
        assert before == after
    status, body = call('GET', '/api/owner/project-archive/1', user=4)
    assert status == 200
    result = json.loads(body)
    assert result['project_name'] == 'Private archive sentinel'
    assert result['interactions'][0]['summary'] == 'Secret conversation'
    assert result['updated_at'] == '2026-05-02T00:00:00Z'
    assert result['archive_history'][0]['by'] == 2
    assert call('GET', '/api/owner/project-archive/2', user=2)[0] == 404


def test_restore_preserves_trash_state_and_links(archive):
    call, factory, _ = archive
    preview_and_archive(call)
    for record_id, destination in [(1, 'projects'), (10, 'trash')]:
        status, body = call('POST', f'/api/owner/project-archive/{record_id}/restore', user=2)
        assert status == 200, body
        assert json.loads(body)['restored_to'] == destination
    with factory() as db:
        assert db.get(Project, 1).client_id == 1
        assert db.get(Project, 10).deleted_at == datetime(2026, 4, 1)
        assert db.get(Interaction, 10).project_id == 1
        assert [e['action'] for e in db.get(Project, 1).archive_history] == ['archived', 'restored']


def test_preview_is_bound_to_owner_contents_and_confirmation(archive):
    call, factory, _ = archive
    status, body = call('GET', '/api/owner/project-archive/preview', user=4)
    token = json.loads(body)['preview_token']
    path = '/api/owner/project-archive/archive'
    payload = {'preview_token': token, 'confirmation': 'ARCHIVE 2'}
    for invalid in (None, 12, {}, 'invalid'):
        assert call('POST', path, {**payload, 'preview_token': invalid}, user=4)[0] == 409
    assert call('POST', path, payload, user=2)[0] == 409
    assert call('POST', path, {**payload, 'confirmation': 'yes'}, user=4)[0] == 400
    with factory() as db:
        db.get(Project, 1).notes = 'Changed after preview'
        db.commit()
    assert call('POST', path, payload, user=4)[0] == 409
    with factory() as db:
        assert db.query(Project).count() == 4


def test_admin_cannot_take_over_owner_or_read_backups(archive):
    call, _, _ = archive
    for path, payload in [('/api/users/4', {'email': 'thief@example.test'}),
                          ('/api/users/4/roles', {'roles': ['admin']}),
                          ('/api/users/4/toggle-active', {})]:
        assert call('PUT', path, payload, user=1)[0] == 403
    # Staging removes whole-database routes entirely.
    assert call('GET', '/api/admin/backups', user=1)[0] in (403, 404)


def test_bulk_writes_and_relationship_changes_cannot_bypass_archive(archive):
    call, factory, _ = archive
    preview_and_archive(call)
    with factory() as db:
        assert db.query(Project).filter(Project.id == 1).update({'project_name': 'stolen'}) == 0
        assert db.query(Project).filter(Project.id == 10).delete() == 0
        assert db.query(Interaction).filter(Interaction.id == 10).update({'summary': 'stolen'}) == 0
        db.rollback()


def test_already_loaded_interaction_cannot_escape_concurrent_archive(archive):
    call, factory, _ = archive
    with factory() as db:
        interaction = db.get(Interaction, 10)
        # End the read transaction while retaining an object held by a caller.
        db.expire_on_commit = False
        db.commit()
        preview_and_archive(call)
        interaction.project_id = None
        with pytest.raises(PermissionError):
            db.flush()
        db.rollback()
        db.add(Interaction(tenant_id=1, project_id=1, summary='Injected'))
        with pytest.raises(PermissionError):
            db.flush()
        db.rollback()
