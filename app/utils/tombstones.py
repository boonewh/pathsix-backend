"""Tombstones: deleted contacts, accounts and interactions stay as rows, hidden.

Offline devices learn about a deletion from its tombstone, so these records are
soft-deleted (deleted_at/deleted_by) and kept at least RETENTION_DAYS before
scripts/prune_tombstones.py removes them.

Reads: every ORM statement that executes SQL, inside or outside a request, hides
tombstones: selects (including column, count and max projections), joins,
subqueries, relationship loads and bulk updates/deletes. Code that must see them
(purge, retention, the future change feed) opts in per statement with the
execution option include_tombstones=True. Not covered: an object already in the
session's identity map (Session.get cache hits, loaded collections, refresh by
primary key), Core table statements and raw SQL. Use a fresh ORM query when a
visibility decision matters.

Writes: before each flush, rows of these models with pending changes are locked
and re-read; if another transaction has tombstoned one, or it no longer exists
or is not visible (purged, or hidden by row security), the flush raises
RecordNotFound. An edit or a second delete racing a delete therefore fails as
"not found" instead of silently changing a tombstone.
"""
from datetime import datetime

from sqlalchemy import event, select
from sqlalchemy.orm import Session, with_loader_criteria

RETENTION_DAYS = 90  # keep equal to scripts/prune_tombstones.py
INCLUDE = 'include_tombstones'


def tombstone_models():
    """In lock order, matching PurgeService and scripts/prune_tombstones.py."""
    from app.models import Account, Contact, Interaction
    return (Interaction, Contact, Account)


def bury(record, user_id):
    """Soft-delete one loaded record. The caller owns the transaction."""
    record.deleted_at = datetime.utcnow()
    record.deleted_by = user_id


def hide_tombstones(state):
    if state.execution_options.get(INCLUDE):
        return
    if not (state.is_select or state.is_update or state.is_delete):
        return
    for model in tombstone_models():
        state.statement = state.statement.options(with_loader_criteria(
            model, model.deleted_at.is_(None), include_aliases=True))


def guard_tombstone_writes(session, *args):
    pending = {}
    for item in session.dirty:
        if isinstance(item, tombstone_models()) and item.id is not None and session.is_modified(item):
            pending.setdefault(type(item), set()).add(item.id)
    from app.services.errors import RecordNotFound
    for model in tombstone_models():
        ids = pending.get(model)
        if not ids:
            continue
        table = model.__table__
        # Lock every row about to be written (ascending id, like purge and the
        # archive guard). Under READ COMMITTED a row tombstoned by a transaction
        # we waited on is returned in its committed, deleted state.
        rows = session.connection().execute(
            select(table.c.id, table.c.deleted_at).where(table.c.id.in_(sorted(ids)))
            .order_by(table.c.id).with_for_update()).all()
        if {row.id for row in rows} != ids or any(row.deleted_at is not None for row in rows):
            raise RecordNotFound(f'{model.__name__} not found')


def install_tombstone_filter():
    for name, callback in (('do_orm_execute', hide_tombstones), ('before_flush', guard_tombstone_writes)):
        if not event.contains(Session, name, callback):
            event.listen(Session, name, callback)
