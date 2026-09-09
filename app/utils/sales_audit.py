"""Transactional attribution for CRM record changes (never infer an actor from assignment)."""
from datetime import datetime
from quart import has_request_context, request
from sqlalchemy import event, inspect
from app.models import ActivityLog, ActivityType, Lead, Client, Project, Interaction, Contact, Account, Subscription, File


def record_change(mapper, connection, target, action):
    if not has_request_context():
        return
    actor = getattr(request, "user", None)
    if actor is None or actor.tenant_id != target.tenant_id:
        return
    if action == ActivityType.edited and not any(
        inspect(target).attrs[attr.key].history.has_changes() for attr in mapper.column_attrs
    ):
        return
    name = next((getattr(target, key, None) for key in
                 ("name", "project_name", "account_name", "plan_name", "filename", "summary", "first_name")
                 if getattr(target, key, None)), None)
    connection.execute(ActivityLog.__table__.insert().values(
        tenant_id=actor.tenant_id, user_id=actor.id, action=action,
        entity_type=mapper.class_.__name__.lower(), entity_id=target.id,
        timestamp=datetime.utcnow(), description=name or f"{mapper.class_.__name__} #{target.id}",
    ))


def created(mapper, connection, target):
    record_change(mapper, connection, target, ActivityType.created)


def edited(mapper, connection, target):
    state = inspect(target)
    soft_deleted = hasattr(target, "deleted_at") and state.attrs.deleted_at.history.has_changes() and target.deleted_at is not None
    record_change(mapper, connection, target, ActivityType.deleted if soft_deleted else ActivityType.edited)


def log_bulk_deletion(session, query):
    for target in query.all():
        record_change(inspect(target).mapper, session.connection(), target, ActivityType.deleted)


def deleted(mapper, connection, target):
    record_change(mapper, connection, target, ActivityType.deleted)


def register_sales_audit():
    for model in (Lead, Client, Project, Interaction, Contact, Account, Subscription, File):
        for event_name, handler in (("after_insert", created), ("after_update", edited), ("after_delete", deleted)):
            if not event.contains(model, event_name, handler):
                event.listen(model, event_name, handler)
