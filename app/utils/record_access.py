"""Shared record and relationship authorization for REST endpoints."""
from quart import abort
from app.models import Client, Lead, Project


def can_access(record, user):
    if record is None or record.tenant_id != user.tenant_id:
        return False
    if any(role.name == "admin" for role in user.roles):
        return True
    if isinstance(record, Project):
        if record.assigned_to is not None:
            return record.assigned_to == user.id
        if record.client_id:
            return can_access(record.client, user)
        if record.lead_id:
            return can_access(record.lead, user)
    return record.created_by == user.id or record.assigned_to == user.id


def require_record(session, model, record_id, user):
    if isinstance(record_id, bool) or not str(record_id).isdigit() or int(record_id) < 1:
        abort(400, description="Invalid relationship ID")
    record = session.query(model).filter(
        model.id == int(record_id), model.tenant_id == user.tenant_id,
        model.deleted_at.is_(None),
    ).first()
    if not can_access(record, user):
        abort(404, description="Related record not found")
    return record


def validate_parents(session, user, data, fields, existing=None, required=True):
    """Validate the final state, including retained parents on partial updates."""
    parents = {field: data.get(field, getattr(existing, field, None)) for field in fields}
    count = sum(value is not None for value in parents.values())
    if count > 1 or (required and count != 1):
        abort(400, description="Invalid parent combination")
    models = {"client_id": Client, "lead_id": Lead, "project_id": Project, "source_lead_id": Lead}
    for field, value in parents.items():
        if value is not None:
            require_record(session, models[field], value, user)
