"""Private archive policy, independent of editable CRM roles and tenant labels."""
import os
import re
from functools import wraps

from quart import request, jsonify
from sqlalchemy import event, select, or_, and_, inspect
from sqlalchemy.orm import Session, with_loader_criteria

ARCHIVE_TENANT_ID = 1
_OWNER_SESSION = object()


def owner_ids():
    # Server configuration only. An empty or malformed setting fails closed.
    raw = os.getenv('PROJECT_ARCHIVE_OWNER_IDS', '')
    parts = raw.split(',')
    if not raw or any(not p.strip().isdigit() or int(p) < 1 for p in parts):
        return frozenset()
    return frozenset(int(p) for p in parts)


def is_archive_owner(user):
    return user is not None and getattr(user, 'is_active', False) and user.id in owner_ids()


def private_session(session):
    """Call only after authenticating an owner. Never accept this from an HTTP flag."""
    if not is_archive_owner(getattr(request, 'user', None)):
        raise PermissionError('Owner access required')
    session.info['project_archive_access'] = _OWNER_SESSION
    return session


def protected_request(user):
    """Block ordinary admins from taking over an owner login or reading DB backups."""
    owner = is_archive_owner(user)
    if request.path.startswith('/api/admin/backups') and not owner:
        return jsonify({'error': 'Owner access required'}), 403
    match = re.match(r'^/api/users/(\d+)(?:/|$)', request.path)
    if (match and request.method not in {'GET', 'HEAD', 'OPTIONS'}
            and int(match[1]) in owner_ids() and not owner):
        return jsonify({'error': 'This owner account cannot be changed by an administrator'}), 403


def requires_archive_owner(fn):
    from app.utils.auth_utils import requires_auth
    @requires_auth()
    @wraps(fn)
    async def wrapped(*args, **kwargs):
        if not is_archive_owner(request.user):
            return jsonify({'error': 'Owner access required'}), 403
        # Staging uses transaction-local RLS identity. Only this authenticated,
        # dedicated ASFI archive request adopts tenant 1, never the normal CRM.
        if hasattr(request, 'principal'):
            from app.services.principal import Principal
            request.principal = Principal(request.user.id, ARCHIVE_TENANT_ID,
                                          frozenset(role.name for role in request.user.roles))
        return await fn(*args, **kwargs)
    return wrapped


def visible_interaction(model):
    from app.models import Project
    projects = Project.__table__
    hidden = select(projects.c.id).where(projects.c.archived_at.isnot(None))
    return or_(model.project_id.is_(None), ~model.project_id.in_(hidden))


def visible_activity(model):
    from app.models import Project, Interaction
    projects, interactions = Project.__table__, Interaction.__table__
    hidden = select(projects.c.id).where(projects.c.archived_at.isnot(None))
    hidden_interactions = select(interactions.c.id).where(interactions.c.project_id.in_(hidden))
    return and_(or_(model.entity_type != 'project', ~model.entity_id.in_(hidden)),
                or_(model.entity_type != 'interaction', ~model.entity_id.in_(hidden_interactions)))


def scope_archive_queries(state):
    if state.session.info.get('project_archive_access') is _OWNER_SESSION:
        return
    from app.models import Project, Interaction, ActivityLog
    for model, condition in ((Project, Project.archived_at.is_(None)),
                             (Interaction, visible_interaction(Interaction)),
                             (ActivityLog, visible_activity(ActivityLog))):
        state.statement = state.statement.options(with_loader_criteria(
            model, condition, include_aliases=True))


def guard_archive_writes(session, *args):
    if session.info.get('project_archive_access') is _OWNER_SESSION:
        return
    from app.models import Project, Interaction
    project_ids = set()
    for item in session.new | session.dirty | session.deleted:
        if isinstance(item, Project):
            if any(inspect(item).attrs[name].history.has_changes()
                   for name in ('archived_at', 'archived_by', 'archive_history')):
                raise PermissionError('Archive changes require the private owner endpoint')
            if item.id is not None:
                project_ids.add(item.id)
        elif isinstance(item, Interaction):
            # A concurrent archive must also stop an already-loaded interaction
            # from being detached from its original Project.
            history = inspect(item).attrs.project_id.history
            project_ids.update(i for i in [item.project_id, *history.deleted] if i is not None)
    if project_ids:
        table = Project.__table__
        rows = session.connection().execute(select(table.c.id, table.c.archived_at)
            .where(table.c.id.in_(sorted(project_ids))).order_by(table.c.id).with_for_update()).all()
        if any(row.archived_at is not None for row in rows):
            raise PermissionError('Archived projects are read-only')


def install_archive_guards():
    for name, callback in [('do_orm_execute', scope_archive_queries), ('before_flush', guard_archive_writes)]:
        if not event.contains(Session, name, callback):
            event.listen(Session, name, callback)
