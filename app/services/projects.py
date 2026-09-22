"""Tenant-bound operations; adapters own commits and notification delivery."""

from app.services.purge import PurgeService
from datetime import datetime
from app.models import Project, ActivityLog, ActivityType, Client, Lead, User
from app.utils.phone_utils import clean_phone_number
from app.schemas.projects import (
    ProjectCreateSchema,
    ProjectUpdateSchema,
    ProjectAssignSchema,
)
from sqlalchemy.orm import joinedload
from sqlalchemy import or_, and_
from app.services.base import TenantService
from app.services.errors import RecordNotFound
from app.services.access import project_access_filter


class ProjectService(TenantService):
    def list_mine(self, page=1, per_page=20, sort_order="newest"):
        self._pagination(page, per_page)
        if sort_order not in ["newest", "oldest", "alphabetical"]:
            sort_order = "newest"
        query = (
            self._query(Project)
            .options(
                joinedload(Project.client),
                joinedload(Project.lead),
                joinedload(Project.assigned_user),
            )
            .filter(
                Project.tenant_id == self.principal.tenant_id,
                Project.deleted_at == None,
            )
            .filter(
                or_(
                    Project.assigned_to == self.principal.user_id,
                    and_(
                        Project.assigned_to == None,
                        or_(
                            and_(
                                Project.client_id != None,
                                or_(
                                    Project.client.has(
                                        Client.assigned_to == self.principal.user_id
                                    ),
                                    and_(
                                        Project.client.has(Client.assigned_to == None),
                                        Project.client.has(
                                            Client.created_by == self.principal.user_id
                                        ),
                                    ),
                                ),
                            ),
                            and_(
                                Project.lead_id != None,
                                or_(
                                    Project.lead.has(
                                        Lead.assigned_to == self.principal.user_id
                                    ),
                                    and_(
                                        Project.lead.has(Lead.assigned_to == None),
                                        Project.lead.has(
                                            Lead.created_by == self.principal.user_id
                                        ),
                                    ),
                                ),
                            ),
                            and_(
                                Project.client_id == None,
                                Project.lead_id == None,
                                Project.created_by == self.principal.user_id,
                            ),
                        ),
                    ),
                )
            )
        )
        query = query.filter(project_access_filter(self.principal))
        if sort_order == "newest":
            query = query.order_by(Project.created_at.desc())
        elif sort_order == "oldest":
            query = query.order_by(Project.created_at.asc())
        elif sort_order == "alphabetical":
            query = query.order_by(Project.project_name.asc())
        total = query.count()
        projects = query.offset((page - 1) * per_page).limit(per_page).all()
        response = {
            "projects": [
                {
                    "id": p.id,
                    "project_name": p.project_name,
                    "type": p.type,
                    "project_status": p.project_status,
                    "project_description": p.project_description,
                    "notes": p.notes,
                    "project_start": p.project_start.isoformat()
                    if p.project_start
                    else None,
                    "project_end": p.project_end.isoformat() if p.project_end else None,
                    "project_worth": p.project_worth,
                    "value_type": p.value_type or "one_time",
                    "client_id": p.client_id,
                    "lead_id": p.lead_id,
                    "client_name": p.client.name if p.client else None,
                    "lead_name": p.lead.name if p.lead else None,
                    "created_at": p.created_at.isoformat() if p.created_at else None,
                    "primary_contact_name": p.primary_contact_name,
                    "primary_contact_title": p.primary_contact_title,
                    "primary_contact_email": p.primary_contact_email,
                    "primary_contact_phone": p.primary_contact_phone,
                    "primary_contact_phone_label": p.primary_contact_phone_label,
                    "assigned_to": p.assigned_to,
                    "assigned_to_name": p.assigned_user.email
                    if p.assigned_user
                    else None,
                }
                for p in projects
            ],
            "total": total,
            "page": page,
            "per_page": per_page,
            "sort_order": sort_order,
        }
        return response

    def detail(self, project_id):
        project = (
            self._query(Project)
            .options(
                joinedload(Project.client),
                joinedload(Project.lead),
                joinedload(Project.assigned_user),
            )
            .filter(
                Project.id == project_id,
                Project.tenant_id == self.principal.tenant_id,
                Project.deleted_at == None,
            )
            .first()
        )
        if not project:
            raise RecordNotFound("Project not found")
        if not self._can_access(project):
            raise RecordNotFound("Project not found")
        return {
            "id": project.id,
            "project_name": project.project_name,
            "type": project.type,
            "project_status": project.project_status,
            "project_description": project.project_description,
            "notes": project.notes,
            "project_start": project.project_start.isoformat() + "Z"
            if project.project_start
            else None,
            "project_end": project.project_end.isoformat() + "Z"
            if project.project_end
            else None,
            "project_worth": project.project_worth,
            "value_type": project.value_type or "one_time",
            "client_id": project.client_id,
            "lead_id": project.lead_id,
            "client_name": project.client.name if project.client else None,
            "lead_name": project.lead.name if project.lead else None,
            "created_by": project.created_by,
            "created_at": project.created_at.isoformat() + "Z"
            if project.created_at
            else None,
            "primary_contact_name": getattr(project, "primary_contact_name", None),
            "primary_contact_title": getattr(project, "primary_contact_title", None),
            "primary_contact_email": getattr(project, "primary_contact_email", None),
            "primary_contact_phone": getattr(project, "primary_contact_phone", None),
            "primary_contact_phone_label": getattr(
                project, "primary_contact_phone_label", None
            ),
            "assigned_to": project.assigned_to,
            "assigned_to_name": project.assigned_user.email
            if project.assigned_user
            else None,
        }

    def create(self, data):
        if not isinstance(data, ProjectCreateSchema):
            raise TypeError("Validated projects data required")
        self._validate_parents(
            data.model_dump(), ("client_id", "lead_id"), required=False
        )
        project = Project(
            tenant_id=self.principal.tenant_id,
            client_id=data.client_id,
            lead_id=data.lead_id,
            project_name=data.project_name,
            type=data.type,
            project_status=data.project_status,
            project_description=data.project_description,
            notes=data.notes,
            project_start=data.project_start,
            project_end=data.project_end,
            project_worth=data.project_worth or 0,
            value_type=data.value_type or "one_time",
            created_by=self.principal.user_id,
            created_at=datetime.utcnow(),
            primary_contact_name=data.primary_contact_name,
            primary_contact_title=data.primary_contact_title,
            primary_contact_email=str(data.primary_contact_email)
            if data.primary_contact_email
            else None,
            primary_contact_phone=clean_phone_number(data.primary_contact_phone)
            if data.primary_contact_phone
            else None,
            primary_contact_phone_label=data.primary_contact_phone_label,
        )
        self.session.add(project)
        self.session.flush()
        return {
            "id": project.id,
            "project_name": project.project_name,
            "type": project.type,
            "project_status": project.project_status,
            "client_name": project.client.name if project.client else None,
            "lead_name": project.lead.name if project.lead else None,
        }

    def update(self, project_id, data):
        if not isinstance(data, ProjectUpdateSchema):
            raise TypeError("Validated projects data required")
        project = (
            self._query(Project)
            .filter(
                Project.id == project_id,
                Project.tenant_id == self.principal.tenant_id,
                Project.deleted_at == None,
            )
            .first()
        )
        if not project:
            raise RecordNotFound("Project not found")
        if not self._can_access(project):
            raise RecordNotFound("Project not found")
        update_data = data.model_dump(exclude_unset=True)
        if update_data.get("project_name", project.project_name) is None:
            raise ValueError("Project name cannot be null")
        if any(
            field in update_data and update_data[field] != getattr(project, field)
            for field in ("client_id", "lead_id")
        ):
            self._validate_parents(
                update_data, ("client_id", "lead_id"), project, required=False
            )

        for field, value in update_data.items():
            if field == "primary_contact_phone":
                cleaned_phone = clean_phone_number(value) if value else None
                setattr(project, field, cleaned_phone)
            elif field == "primary_contact_email":
                setattr(project, field, str(value) if value else None)
            elif field == "project_worth":
                setattr(project, field, value or 0)
            else:
                setattr(project, field, value)
        project.last_updated_by = self.principal.user_id
        project.updated_at = datetime.utcnow()
        self.session.flush()
        return {
            "id": project.id,
            "project_name": project.project_name,
            "type": project.type,
            "project_status": project.project_status,
            "client_name": project.client.name if project.client else None,
            "lead_name": project.lead.name if project.lead else None,
        }

    def assign(self, project_id, data):
        self._require_admin()
        if not isinstance(data, ProjectAssignSchema):
            raise TypeError("Validated projects data required")
        project = (
            self._query(Project)
            .filter(
                Project.id == project_id,
                Project.tenant_id == self.principal.tenant_id,
                Project.deleted_at == None,
            )
            .first()
        )
        if not project:
            raise RecordNotFound("Project not found")
        assigned_user = (
            self._query(User)
            .filter(
                User.id == data.assigned_to,
                User.tenant_id == self.principal.tenant_id,
                User.is_active == True,
            )
            .first()
        )
        if not assigned_user:
            raise ValueError(f"User {data.assigned_to} not found or not active")
        project.assigned_to = data.assigned_to
        project.last_updated_by = self.principal.user_id
        project.updated_at = datetime.utcnow()
        self.session.flush()
        return {
            "to_email": assigned_user.email,
            "entity_type": "project",
            "entity_name": project.project_name,
            "assigned_to": project.assigned_to,
        }

    def interaction_link(self, project_id):
        project = (
            self._query(Project)
            .filter(
                Project.id == project_id, Project.tenant_id == self.principal.tenant_id
            )
            .first()
        )
        if not project:
            raise RecordNotFound("Project not found")
        if project.deleted_at is not None or not self._can_access(project):
            raise RecordNotFound("Project not found")
        return {
            "redirect": f"/api/interactions/?project_id={project_id}",
            "message": "Use the main interactions endpoint with project_id parameter",
        }

    def list_all(self, page=1, per_page=20, sort_order="newest", user_email=None):
        self._require_admin()
        self._pagination(page, per_page)
        if sort_order not in ["newest", "oldest", "alphabetical"]:
            sort_order = "newest"
        query = (
            self._query(Project)
            .options(
                joinedload(Project.assigned_user),
                joinedload(Project.client).joinedload(Client.assigned_user),
                joinedload(Project.client).joinedload(Client.created_by_user),
                joinedload(Project.lead).joinedload(Lead.assigned_user),
                joinedload(Project.lead).joinedload(Lead.created_by_user),
            )
            .filter(Project.tenant_id == self.principal.tenant_id)
        )
        query = query.filter(
            Project.deleted_at.is_(None), project_access_filter(self.principal)
        )
        if user_email:
            subquery_user_id = (
                self._query(User.id).filter(User.email == user_email).scalar_subquery()
            )
            query = query.filter(
                or_(
                    and_(
                        Project.client_id != None,
                        or_(
                            Project.client.has(
                                Client.assigned_user.has(
                                    and_(
                                        User.email == user_email,
                                        User.tenant_id == self.principal.tenant_id,
                                    )
                                )
                            ),
                            Project.client.has(
                                Client.created_by_user.has(
                                    and_(
                                        User.email == user_email,
                                        User.tenant_id == self.principal.tenant_id,
                                    )
                                )
                            ),
                        ),
                    ),
                    and_(
                        Project.lead_id != None,
                        or_(
                            Project.lead.has(
                                Lead.assigned_user.has(
                                    and_(
                                        User.email == user_email,
                                        User.tenant_id == self.principal.tenant_id,
                                    )
                                )
                            ),
                            Project.lead.has(
                                Lead.created_by_user.has(
                                    and_(
                                        User.email == user_email,
                                        User.tenant_id == self.principal.tenant_id,
                                    )
                                )
                            ),
                        ),
                    ),
                    and_(
                        Project.client_id == None,
                        Project.lead_id == None,
                        Project.created_by == subquery_user_id,
                    ),
                )
            )
        if sort_order == "newest":
            query = query.order_by(Project.created_at.desc())
        elif sort_order == "oldest":
            query = query.order_by(Project.created_at.asc())
        elif sort_order == "alphabetical":
            query = query.order_by(Project.project_name.asc())
        total = query.count()
        projects = query.offset((page - 1) * per_page).limit(per_page).all()
        response_data = {"projects": []}
        for p in projects:
            if p.assigned_user:
                assigned_to_email = p.assigned_user.email
            elif p.client and p.client.assigned_user:
                assigned_to_email = p.client.assigned_user.email
            elif p.client and p.client.created_by_user:
                assigned_to_email = p.client.created_by_user.email
            elif p.lead and p.lead.assigned_user:
                assigned_to_email = p.lead.assigned_user.email
            elif p.lead and p.lead.created_by_user:
                assigned_to_email = p.lead.created_by_user.email
            else:
                assigned_to_email = None
            response_data["projects"].append(
                {
                    "id": p.id,
                    "project_name": p.project_name,
                    "type": p.type,
                    "project_status": p.project_status,
                    "project_description": p.project_description,
                    "notes": p.notes,
                    "project_start": p.project_start.isoformat()
                    if p.project_start
                    else None,
                    "project_end": p.project_end.isoformat() if p.project_end else None,
                    "project_worth": p.project_worth,
                    "client_id": p.client_id,
                    "lead_id": p.lead_id,
                    "client_name": p.client.name if p.client else None,
                    "lead_name": p.lead.name if p.lead else None,
                    "assigned_to_email": assigned_to_email,
                    "created_at": p.created_at.isoformat() if p.created_at else None,
                    "primary_contact_name": p.primary_contact_name,
                    "primary_contact_title": p.primary_contact_title,
                    "primary_contact_email": p.primary_contact_email,
                    "primary_contact_phone": p.primary_contact_phone,
                    "primary_contact_phone_label": p.primary_contact_phone_label,
                }
            )
        response_data.update(
            {
                "total": total,
                "page": page,
                "per_page": per_page,
                "sort_order": sort_order,
                "user_email": user_email,
            }
        )
        response = response_data
        return response

    def by_client(self, client_id):
        client = (
            self._query(Client)
            .filter(
                Client.id == client_id,
                Client.tenant_id == self.principal.tenant_id,
                Client.deleted_at == None,
            )
            .first()
        )
        if not client:
            raise RecordNotFound("Client not found")
        if not self.principal.is_admin:
            if (
                client.assigned_to != self.principal.user_id
                and client.created_by != self.principal.user_id
            ):
                raise PermissionError("Forbidden")
        projects = (
            self._query(Project)
            .filter(
                project_access_filter(self.principal),
                Project.client_id == client_id,
                Project.tenant_id == self.principal.tenant_id,
                Project.deleted_at == None,
            )
            .order_by(Project.created_at.desc())
            .all()
        )
        return [
            {
                "id": p.id,
                "type": p.type,
                "project_name": p.project_name,
                "project_status": p.project_status,
                "project_description": p.project_description,
                "notes": p.notes,
                "project_start": p.project_start.isoformat()
                if p.project_start
                else None,
                "project_end": p.project_end.isoformat() if p.project_end else None,
                "project_worth": p.project_worth,
                "value_type": p.value_type or "one_time",
                "created_at": p.created_at.isoformat() if p.created_at else None,
                "primary_contact_name": p.primary_contact_name,
                "primary_contact_title": p.primary_contact_title,
                "primary_contact_email": p.primary_contact_email,
                "primary_contact_phone": p.primary_contact_phone,
                "primary_contact_phone_label": p.primary_contact_phone_label,
            }
            for p in projects
        ]

    def by_lead(self, lead_id):
        lead = (
            self._query(Lead)
            .filter(
                Lead.id == lead_id,
                Lead.tenant_id == self.principal.tenant_id,
                Lead.deleted_at == None,
            )
            .first()
        )
        if not lead:
            raise RecordNotFound("Lead not found")
        if not self.principal.is_admin:
            if (
                lead.assigned_to != self.principal.user_id
                and lead.created_by != self.principal.user_id
            ):
                raise PermissionError("Forbidden")
        projects = (
            self._query(Project)
            .filter(
                project_access_filter(self.principal),
                Project.lead_id == lead_id,
                Project.tenant_id == self.principal.tenant_id,
                Project.deleted_at == None,
            )
            .order_by(Project.created_at.desc())
            .all()
        )
        return [
            {
                "id": p.id,
                "project_name": p.project_name,
                "type": p.type,
                "project_status": p.project_status,
                "project_description": p.project_description,
                "notes": p.notes,
                "project_start": p.project_start.isoformat()
                if p.project_start
                else None,
                "project_end": p.project_end.isoformat() if p.project_end else None,
                "project_worth": p.project_worth,
                "value_type": p.value_type or "one_time",
                "created_at": p.created_at.isoformat() if p.created_at else None,
                "primary_contact_name": p.primary_contact_name,
                "primary_contact_title": p.primary_contact_title,
                "primary_contact_email": p.primary_contact_email,
                "primary_contact_phone": p.primary_contact_phone,
                "primary_contact_phone_label": p.primary_contact_phone_label,
            }
            for p in projects
        ]

    def delete(self, project_id):
        project = (
            self._query(Project)
            .filter(
                Project.id == project_id, Project.tenant_id == self.principal.tenant_id
            )
            .first()
        )
        if not project:
            raise RecordNotFound("Project not found")
        if not self._can_access(project):
            raise RecordNotFound("Project not found")
        if project.deleted_at is not None:
            return {"message": "Project already deleted"}
        project.deleted_at = datetime.utcnow()
        project.deleted_by = self.principal.user_id
        self.session.flush()
        return {"message": "Project soft-deleted successfully"}

    def list_trash(self):
        if not self.principal.is_admin:
            trashed = (
                self._query(Project)
                .filter(
                    Project.tenant_id == self.principal.tenant_id,
                    Project.deleted_at != None,
                    Project.created_by == self.principal.user_id,
                )
                .order_by(Project.deleted_at.desc())
                .all()
            )
        else:
            trashed = (
                self._query(Project)
                .filter(
                    Project.tenant_id == self.principal.tenant_id,
                    Project.deleted_at != None,
                )
                .order_by(Project.deleted_at.desc())
                .all()
            )
        return [
            {
                "id": p.id,
                "name": p.project_name,
                "deleted_at": p.deleted_at.isoformat() + "Z",
                "deleted_by": p.deleted_by,
            }
            for p in trashed
        ]

    def restore(self, project_id):
        query = self._query(Project).filter(
            Project.id == project_id,
            Project.tenant_id == self.principal.tenant_id,
            Project.deleted_at != None,
        )
        if not self.principal.is_admin:
            query = query.filter(Project.created_by == self.principal.user_id)
        project = query.first()
        if not project:
            raise RecordNotFound("Project not found or not authorized to restore")
        project.deleted_at = None
        project.deleted_by = None
        self.session.flush()
        return {"message": "Project restored successfully"}

    def purge(self, project_id):
        PurgeService(self.session, self.principal).purge("projects", [project_id], single=True)
        return {"message": "Project permanently deleted"}

    def bulk_delete(self, project_ids):
        self._require_admin()
        self._ids(project_ids)
        from app.utils.sales_audit import log_bulk_deletion
        log_bulk_deletion(self.session, self.session.query(Project).filter(
            Project.tenant_id == self.principal.tenant_id, Project.id.in_(project_ids), Project.deleted_at.is_(None)))
        updated_count = (
            self._query(Project)
            .filter(
                Project.tenant_id == self.principal.tenant_id,
                Project.id.in_(project_ids),
                Project.deleted_at == None,
            )
            .update(
                {
                    Project.deleted_at: datetime.utcnow(),
                    Project.deleted_by: self.principal.user_id,
                },
                synchronize_session=False,
            )
        )
        self.session.flush()
        return {"message": f"{updated_count} project(s) deleted"}

    def bulk_purge(self, project_ids):
        result = PurgeService(self.session, self.principal).purge("projects", project_ids)
        count = len(result["deleted_ids"])
        return {"message": f"{count} project(s) permanently deleted"}

    def record_view(self, project_id):
        project = self._require_record(Project, project_id)
        self.session.add(
            ActivityLog(
                tenant_id=self.principal.tenant_id,
                user_id=self.principal.user_id,
                action=ActivityType.viewed,
                entity_type="project",
                entity_id=project.id,
                description=f"Viewed project '{project.project_name}'",
            )
        )
