"""Tombstones: deleted contacts, accounts and interactions stay as rows, hidden.

Offline devices learn about a deletion from its tombstone, so these records are
soft-deleted (deleted_at/deleted_by) and kept at least RETENTION_DAYS before
scripts/prune_tombstones.py removes them. Every ORM statement, inside or outside
a request, hides tombstones: selects, joins, relationship loads and bulk
updates/deletes. Code that must see them (purge, retention, the future change
feed) opts in per statement with the execution option include_tombstones=True.
Raw SQL is not filtered.
"""
from datetime import datetime

from sqlalchemy import event
from sqlalchemy.orm import Session, with_loader_criteria

RETENTION_DAYS = 90  # keep equal to scripts/prune_tombstones.py
INCLUDE = 'include_tombstones'


def tombstone_models():
    from app.models import Account, Contact, Interaction
    return (Contact, Account, Interaction)


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


def install_tombstone_filter():
    if not event.contains(Session, 'do_orm_execute', hide_tombstones):
        event.listen(Session, 'do_orm_execute', hide_tombstones)
