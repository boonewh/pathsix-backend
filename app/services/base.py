"""Shared tenant-scoped queries and parent authorization for CRM services."""

from sqlalchemy.orm import with_loader_criteria
from app.database import Base
from app.models import Client, Lead, Project
from app.services.principal import Principal
from app.services.database_context import bind_principal
from app.services.errors import RecordNotFound
from app.services.access import owned_record_filter, project_access_filter


class TenantService:
    def __init__(self, session, principal: Principal):
        if not isinstance(principal, Principal):
            raise TypeError("An authenticated principal is required")
        self.session = session
        self.principal = principal
        bind_principal(session, principal)

    def _query(self, *entities):
        query = self.session.query(*entities)
        tenant_id = self.principal.tenant_id
        # Explicitly scope root entities, joins and later relationship loads without HTTP.
        for description in query.column_descriptions:
            model = description.get("entity")
            if model is not None and hasattr(model, "tenant_id"):
                query = query.filter(model.tenant_id == tenant_id)
        for mapper in Base.registry.mappers:
            model = mapper.class_
            if hasattr(model, "tenant_id"):
                query = query.options(
                    with_loader_criteria(
                        model, model.tenant_id == tenant_id, include_aliases=True
                    )
                )
        return query

    def _require_admin(self):
        if not self.principal.is_admin:
            raise PermissionError("Administrator access required")

    def _can_access(self, record):
        if record is None or record.tenant_id != self.principal.tenant_id:
            return False
        model = type(record)
        predicate = (
            project_access_filter(self.principal)
            if model is Project
            else owned_record_filter(model, self.principal)
        )
        return (
            self._query(model).filter(model.id == record.id, predicate).first()
            is not None
        )

    def _require_record(self, model, record_id):
        if (
            isinstance(record_id, bool)
            or not str(record_id).isdigit()
            or int(record_id) < 1
        ):
            raise ValueError("Invalid relationship ID")
        record = (
            self._query(model)
            .filter(model.id == int(record_id), model.deleted_at.is_(None))
            .first()
        )
        if not self._can_access(record):
            raise RecordNotFound("Related record not found")
        return record

    def _validate_parents(self, data, fields, existing=None, required=True):
        values = {
            field: data.get(field, getattr(existing, field, None)) for field in fields
        }
        count = sum(value is not None for value in values.values())
        if count > 1 or (required and count != 1):
            raise ValueError("Invalid parent combination")
        models = {"client_id": Client, "lead_id": Lead, "project_id": Project}
        for field, value in values.items():
            if value is not None:
                self._require_record(models[field], value)

    @staticmethod
    def _pagination(page, per_page):
        if (
            type(page) is not int
            or type(per_page) is not int
            or page < 1
            or not 1 <= per_page <= 200
        ):
            raise ValueError("Invalid pagination")

    @staticmethod
    def _ids(values):
        if (
            not isinstance(values, list)
            or not values
            or any(type(v) is not int or v < 1 for v in values)
        ):
            raise ValueError("Positive integer record IDs are required")
