"""Tenant-bound operations; adapters own commits and notification delivery."""

from sqlalchemy.orm import joinedload
from sqlalchemy import or_, and_, func
from icalendar import Calendar, Event
from app.models import (
    Interaction,
    Client,
    Lead,
    Project,
    FollowUpStatus,
    User,
)
from app.schemas.interactions import InteractionCreateSchema, InteractionUpdateSchema
from app.services.base import TenantService
from app.services.errors import RecordNotFound
from app.services.access import interaction_access_filter


class InteractionService(TenantService):
    def list_visible(
        self,
        client_id=None,
        lead_id=None,
        project_id=None,
        page=1,
        per_page=10,
        sort_order="newest",
    ):
        self._pagination(page, per_page)
        if client_id == "None":
            client_id = None
        if lead_id == "None":
            lead_id = None
        if project_id == "None":
            project_id = None
        entity_count = sum((bool(x) for x in [client_id, lead_id, project_id]))
        if entity_count > 1:
            raise ValueError("Cannot filter by multiple entity types")
        valid_sorts = ["newest", "oldest", "pending", "completed"]
        if sort_order not in valid_sorts:
            sort_order = "newest"
        query = (
            self._query(Interaction)
            .options(
                joinedload(Interaction.client),
                joinedload(Interaction.lead),
                joinedload(Interaction.project),
            )
            .filter(Interaction.tenant_id == self.principal.tenant_id)
        )
        query = query.filter(interaction_access_filter(self.principal))
        if client_id:
            query = query.filter(
                Interaction.client_id == int(client_id),
                Interaction.lead_id == None,
                Interaction.project_id == None,
            )
        elif lead_id:
            query = query.filter(
                Interaction.lead_id == int(lead_id),
                Interaction.client_id == None,
                Interaction.project_id == None,
            )
        elif project_id:
            query = query.filter(
                Interaction.project_id == int(project_id),
                Interaction.client_id == None,
                Interaction.lead_id == None,
            )
        if sort_order == "newest":
            query = query.order_by(Interaction.contact_date.desc())
        elif sort_order == "oldest":
            query = query.order_by(Interaction.contact_date.asc())
        elif sort_order == "pending":
            query = query.order_by(
                and_(
                    Interaction.follow_up != None,
                    Interaction.followup_status != FollowUpStatus.completed,
                ).desc(),
                Interaction.follow_up.asc(),
                Interaction.contact_date.desc(),
            )
        elif sort_order == "completed":
            query = query.order_by(
                (Interaction.followup_status == FollowUpStatus.completed).desc(),
                Interaction.contact_date.desc(),
            )
        total = query.count()
        interactions = query.offset((page - 1) * per_page).limit(per_page).all()
        response_data = {
            "interactions": [
                {
                    "id": i.id,
                    "contact_date": i.contact_date.isoformat(),
                    "follow_up": i.follow_up.isoformat() if i.follow_up else None,
                    "summary": i.summary,
                    "outcome": i.outcome,
                    "notes": i.notes,
                    "client_id": i.client_id,
                    "lead_id": i.lead_id,
                    "project_id": i.project_id,
                    "client_name": i.client.name if i.client else None,
                    "lead_name": i.lead.name if i.lead else None,
                    "project_name": i.project.project_name if i.project else None,
                    "contact_person": i.contact_person
                    or (i.client.contact_person if i.client else None)
                    or (i.lead.contact_person if i.lead else None)
                    or (i.project.primary_contact_name if i.project else None),
                    "email": i.email
                    or (i.client.email if i.client else None)
                    or (i.lead.email if i.lead else None)
                    or (i.project.primary_contact_email if i.project else None),
                    "phone": i.phone
                    or (i.client.phone if i.client else None)
                    or (i.lead.phone if i.lead else None)
                    or (i.project.primary_contact_phone if i.project else None),
                    "phone_label": (i.client.phone_label if i.client else None)
                    or (i.lead.phone_label if i.lead else None)
                    or (i.project.primary_contact_phone_label if i.project else None)
                    or "work",
                    "secondary_phone": (i.client.secondary_phone if i.client else None)
                    or (i.lead.secondary_phone if i.lead else None),
                    "secondary_phone_label": (
                        i.client.secondary_phone_label if i.client else None
                    )
                    or (i.lead.secondary_phone_label if i.lead else None),
                    "followup_status": i.followup_status.value
                    if i.followup_status
                    else None,
                    "profile_link": f"/clients/{i.client_id}"
                    if i.client_id
                    else f"/leads/{i.lead_id}"
                    if i.lead_id
                    else f"/projects/{i.project_id}"
                    if i.project_id
                    else None,
                }
                for i in interactions
            ],
            "total": total,
            "page": page,
            "per_page": per_page,
            "sort_order": sort_order,
        }
        response = response_data
        return response

    def create(self, data):
        if not isinstance(data, InteractionCreateSchema):
            raise TypeError("Validated interactions data required")
        self._validate_parents(
            data.model_dump(), ("client_id", "lead_id", "project_id")
        )
        interaction = Interaction(
            tenant_id=self.principal.tenant_id,
            client_id=data.client_id,
            lead_id=data.lead_id,
            project_id=data.project_id,
            contact_date=data.contact_date,
            summary=data.summary,
            outcome=data.outcome,
            notes=data.notes,
            follow_up=data.follow_up,
            contact_person=data.contact_person,
            email=str(data.email) if data.email else None,
            phone=data.phone,
            followup_status=data.followup_status or FollowUpStatus.pending,
        )
        self.session.add(interaction)
        self.session.flush()
        return {"id": interaction.id}

    def update(self, interaction_id, data):
        if not isinstance(data, InteractionUpdateSchema):
            raise TypeError("Validated interactions data required")
        interaction = (
            self._query(Interaction)
            .options(
                joinedload(Interaction.client),
                joinedload(Interaction.lead),
                joinedload(Interaction.project),
            )
            .filter(
                Interaction.id == interaction_id,
                Interaction.tenant_id == self.principal.tenant_id,
            )
            .first()
        )
        if not interaction:
            raise RecordNotFound("Interaction not found")
        self._validate_parents({}, ("client_id", "lead_id", "project_id"), interaction)
        update_data = data.model_dump(exclude_unset=True)
        if any(
            field in update_data and update_data[field] is None
            for field in ("summary", "contact_date")
        ):
            raise ValueError("Summary and contact date cannot be null")
        self._validate_parents(
            update_data, ("client_id", "lead_id", "project_id"), interaction
        )
        for field, value in update_data.items():
            if field == "email":
                interaction.email = str(value) if value else None
            else:
                setattr(interaction, field, value)
        self.session.flush()
        return {"id": interaction.id}

    def delete(self, interaction_id):
        interaction = (
            self._query(Interaction)
            .options(
                joinedload(Interaction.client),
                joinedload(Interaction.lead),
                joinedload(Interaction.project),
            )
            .filter(
                Interaction.id == interaction_id,
                Interaction.tenant_id == self.principal.tenant_id,
            )
            .first()
        )
        if not interaction:
            raise RecordNotFound("Interaction not found")
        self._validate_parents({}, ("client_id", "lead_id", "project_id"), interaction)
        self.session.delete(interaction)
        self.session.flush()
        return {"message": "Interaction deleted"}

    def transfer(self, from_lead_id, to_client_id):
        self._require_record(Lead, from_lead_id)
        self._require_record(Client, to_client_id)
        interactions = (
            self._query(Interaction)
            .filter(
                Interaction.tenant_id == self.principal.tenant_id,
                Interaction.lead_id == int(from_lead_id),
                Interaction.client_id.is_(None),
                Interaction.project_id.is_(None),
            )
            .all()
        )
        for interaction in interactions:
            interaction.lead_id = None
            interaction.client_id = int(to_client_id)
        self.session.flush()
        return {"success": True, "transferred": len(interactions)}

    def calendar(self, interaction_id):
        interaction = (
            self._query(Interaction)
            .options(
                joinedload(Interaction.client),
                joinedload(Interaction.lead),
                joinedload(Interaction.project),
            )
            .filter(
                Interaction.id == interaction_id,
                Interaction.tenant_id == self.principal.tenant_id,
            )
            .first()
        )
        if not interaction:
            raise RecordNotFound("Interaction not found")
        self._validate_parents({}, ("client_id", "lead_id", "project_id"), interaction)
        if not interaction.follow_up:
            raise ValueError("This interaction has no follow-up date")
        cal = Calendar()
        cal.add("prodid", "-//PathSix CRM//EN")
        cal.add("version", "2.0")
        entity_name = (
            interaction.client.name
            if interaction.client
            else interaction.lead.name
            if interaction.lead
            else interaction.project.project_name
            if interaction.project
            else "CRM Entity"
        )
        contact_name = (
            interaction.contact_person
            or (interaction.client.contact_person if interaction.client else None)
            or (interaction.lead.contact_person if interaction.lead else None)
            or (
                interaction.project.primary_contact_name
                if interaction.project
                else None
            )
            or "Contact"
        )
        event = Event()
        event.add("summary", f"Follow-up: {entity_name} - {contact_name}")
        event.add("dtstart", interaction.follow_up)
        event.add("dtend", interaction.follow_up)
        event.add("dtstamp", interaction.contact_date)
        event.add(
            "description",
            f"Outcome: {interaction.outcome or ''}\nNotes: {interaction.notes or ''}",
        )
        location_parts = []
        if (
            interaction.phone
            or (interaction.client and interaction.client.phone)
            or (interaction.lead and interaction.lead.phone)
            or (interaction.project and interaction.project.primary_contact_phone)
        ):
            phone = (
                interaction.phone
                or (interaction.client.phone if interaction.client else None)
                or (interaction.lead.phone if interaction.lead else None)
                or (
                    interaction.project.primary_contact_phone
                    if interaction.project
                    else None
                )
            )
            location_parts.append(f"Phone: {phone}")
        if (
            interaction.email
            or (interaction.client and interaction.client.email)
            or (interaction.lead and interaction.lead.email)
            or (interaction.project and interaction.project.primary_contact_email)
        ):
            email = (
                interaction.email
                or (interaction.client.email if interaction.client else None)
                or (interaction.lead.email if interaction.lead else None)
                or (
                    interaction.project.primary_contact_email
                    if interaction.project
                    else None
                )
            )
            location_parts.append(f"Email: {email}")
        event.add("location", "\n".join(location_parts))
        event["uid"] = f"interaction-{interaction.id}@pathsixcrm"
        cal.add_component(event)
        ics_content = cal.to_ical()
        return ics_content

    def complete(self, interaction_id):
        interaction = (
            self._query(Interaction)
            .options(
                joinedload(Interaction.client),
                joinedload(Interaction.lead),
                joinedload(Interaction.project),
            )
            .filter(
                Interaction.id == interaction_id,
                Interaction.tenant_id == self.principal.tenant_id,
            )
            .first()
        )
        if not interaction:
            raise RecordNotFound("Interaction not found")
        self._validate_parents({}, ("client_id", "lead_id", "project_id"), interaction)
        interaction.followup_status = FollowUpStatus.completed
        self.session.flush()
        return {"message": "Interaction marked as completed"}

    def list_all(self, page=1, per_page=20, sort_order="newest", user_email=None):
        self._require_admin()
        self._pagination(page, per_page)
        if sort_order not in ["newest", "oldest", "alphabetical"]:
            sort_order = "newest"
        query = (
            self._query(Interaction)
            .options(
                joinedload(Interaction.client).joinedload(Client.assigned_user),
                joinedload(Interaction.client).joinedload(Client.created_by_user),
                joinedload(Interaction.lead).joinedload(Lead.assigned_user),
                joinedload(Interaction.lead).joinedload(Lead.created_by_user),
                joinedload(Interaction.project).joinedload(Project.assigned_user),
                joinedload(Interaction.project).joinedload(Project.created_by_user),
            )
            .filter(Interaction.tenant_id == self.principal.tenant_id)
        )
        query = query.filter(interaction_access_filter(self.principal))
        if user_email:
            subquery_user_id = (
                self._query(User.id).filter(User.email == user_email).scalar_subquery()
            )
            query = query.filter(
                or_(
                    and_(
                        Interaction.client_id != None,
                        Interaction.client.has(
                            Client.assigned_user.has(
                                and_(
                                    User.email == user_email,
                                    User.tenant_id == self.principal.tenant_id,
                                )
                            )
                        ),
                    ),
                    and_(
                        Interaction.client_id != None,
                        Interaction.client.has(
                            and_(
                                Client.assigned_to == None,
                                Client.created_by_user.has(
                                    and_(
                                        User.email == user_email,
                                        User.tenant_id == self.principal.tenant_id,
                                    )
                                ),
                            )
                        ),
                    ),
                    and_(
                        Interaction.lead_id != None,
                        Interaction.lead.has(
                            Lead.assigned_user.has(
                                and_(
                                    User.email == user_email,
                                    User.tenant_id == self.principal.tenant_id,
                                )
                            )
                        ),
                    ),
                    and_(
                        Interaction.lead_id != None,
                        Interaction.lead.has(
                            and_(
                                Lead.assigned_to == None,
                                Lead.created_by_user.has(
                                    and_(
                                        User.email == user_email,
                                        User.tenant_id == self.principal.tenant_id,
                                    )
                                ),
                            )
                        ),
                    ),
                    and_(
                        Interaction.project_id != None,
                        Interaction.project.has(
                            Project.assigned_to == subquery_user_id
                        ),
                    ),
                    and_(
                        Interaction.project_id != None,
                        Interaction.project.has(
                            and_(
                                Project.assigned_to == None,
                                Project.created_by == subquery_user_id,
                            )
                        ),
                    ),
                )
            )
        if sort_order == "newest":
            query = query.order_by(Interaction.contact_date.desc())
        elif sort_order == "oldest":
            query = query.order_by(Interaction.contact_date.asc())
        elif sort_order == "alphabetical":
            query = (
                query.order_by(
                    func.coalesce(Client.name, Lead.name, Project.project_name).asc()
                )
                .outerjoin(Client, Interaction.client_id == Client.id)
                .outerjoin(Lead, Interaction.lead_id == Lead.id)
                .outerjoin(Project, Interaction.project_id == Project.id)
            )
        total = query.count()
        interactions = query.offset((page - 1) * per_page).limit(per_page).all()
        response_data = {
            "interactions": [
                {
                    "id": i.id,
                    "contact_date": i.contact_date.isoformat(),
                    "follow_up": i.follow_up.isoformat() if i.follow_up else None,
                    "summary": i.summary,
                    "outcome": i.outcome,
                    "notes": i.notes,
                    "client_id": i.client_id,
                    "lead_id": i.lead_id,
                    "project_id": i.project_id,
                    "client_name": i.client.name if i.client else None,
                    "lead_name": i.lead.name if i.lead else None,
                    "project_name": i.project.project_name if i.project else None,
                    "contact_person": i.contact_person.strip()
                    if i.contact_person and i.contact_person.strip()
                    else i.client.contact_person
                    if i.client
                    else i.lead.contact_person
                    if i.lead
                    else i.project.primary_contact_name
                    if i.project
                    else None,
                    "email": i.email
                    or (i.client.email if i.client else None)
                    or (i.lead.email if i.lead else None)
                    or (i.project.primary_contact_email if i.project else None),
                    "phone": i.phone
                    or (i.client.phone if i.client else None)
                    or (i.lead.phone if i.lead else None)
                    or (i.project.primary_contact_phone if i.project else None),
                    "followup_status": i.followup_status.value
                    if i.followup_status
                    else None,
                    "profile_link": f"/clients/{i.client_id}"
                    if i.client_id
                    else f"/leads/{i.lead_id}"
                    if i.lead_id
                    else f"/projects/{i.project_id}"
                    if i.project_id
                    else None,
                    "assigned_to_name": i.client.assigned_user.email
                    if i.client and i.client.assigned_user
                    else i.client.created_by_user.email
                    if i.client and i.client.created_by_user
                    else i.lead.assigned_user.email
                    if i.lead and i.lead.assigned_user
                    else i.lead.created_by_user.email
                    if i.lead and i.lead.created_by_user
                    else i.project.assigned_user.email
                    if i.project and i.project.assigned_user
                    else i.project.created_by_user.email
                    if i.project and i.project.created_by_user
                    else None,
                }
                for i in interactions
            ],
            "total": total,
            "page": page,
            "per_page": per_page,
            "sort_order": sort_order,
            "user_email": user_email,
        }
        response = response_data
        return response
