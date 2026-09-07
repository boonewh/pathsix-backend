"""SQL permissions shared by tenant-bound record services."""
from sqlalchemy import and_, or_
from app.services.principal import Principal


def owned_record_filter(model, principal: Principal, *, include_deleted=False):
    predicates = [model.tenant_id == principal.tenant_id]
    if not include_deleted:
        predicates.append(model.deleted_at.is_(None))
    if not principal.is_admin:
        predicates.append(or_(model.created_by == principal.user_id,
                              model.assigned_to == principal.user_id))
    return and_(*predicates)
