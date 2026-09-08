"""Lead operations with explicit tenant context and caller-owned transactions.

Methods never commit. Adapters commit once after a successful operation; failures
must roll back. Detail reads are pure; web activity logging is a separate method.
"""
from datetime import datetime
from app.models import Lead, Contact, ActivityLog, ActivityType, User
from sqlalchemy import or_, and_
from app.schemas.leads import LeadCreateSchema, LeadUpdateSchema
from app.services.principal import Principal
from app.services.access import owned_record_filter
from app.utils.phone_utils import clean_phone_number
from app.services.errors import RecordNotFound


class LeadService:
    def __init__(self, session, principal: Principal):
        if not isinstance(principal, Principal):
            raise TypeError("An authenticated principal is required")
        self.session = session
        self.principal = principal
        from app.services.database_context import bind_principal
        bind_principal(session, principal)

    def _get(self, lead_id, *, include_deleted=False):
        lead = self.session.query(Lead).filter(
            Lead.id == lead_id,
            owned_record_filter(Lead, self.principal, include_deleted=include_deleted),
        ).first()
        if lead is None:
            raise RecordNotFound("Lead not found")
        return lead

    def create(self, data: LeadCreateSchema):
        if not isinstance(data, LeadCreateSchema):
            raise TypeError("Validated lead data required")
        fields = data.model_dump()
        for field in ('phone', 'secondary_phone'):
            fields[field] = clean_phone_number(fields[field]) if fields[field] else None
        fields['email'] = str(data.email) if data.email else None
        lead = Lead(**fields, tenant_id=self.principal.tenant_id,
                        created_by=self.principal.user_id)
        self.session.add(lead)
        self.session.flush()
        return lead.id

    def update(self, lead_id, data: LeadUpdateSchema):
        if not isinstance(data, LeadUpdateSchema):
            raise TypeError("Validated lead data required")
        lead = self._get(lead_id)
        fields = data.model_dump(exclude_unset=True)
        if 'name' in fields and fields['name'] is None:
            raise ValueError("Lead name cannot be null")
        for field, value in fields.items():
            if field in ('phone', 'secondary_phone'):
                value = clean_phone_number(value) if value else None
            elif field == 'email':
                value = str(value) if value else None
            if field == "lead_status" and value == "won" and lead.lead_status != "won":
                lead.converted_on = datetime.utcnow()
            setattr(lead, field, value)
        lead.updated_by = self.principal.user_id
        lead.updated_at = datetime.utcnow()
        self.session.flush()
        return lead.id

    def delete(self, lead_id):
        lead = self._get(lead_id, include_deleted=True)
        if lead.deleted_at is not None:
            return False
        lead.deleted_at = datetime.utcnow()
        lead.deleted_by = self.principal.user_id
        self.session.flush()
        return True

    def restore(self, lead_id):
        lead = self._get(lead_id, include_deleted=True)
        if lead.deleted_at is None:
            raise RecordNotFound("Lead not found or not authorized to restore")
        lead.deleted_at = None
        lead.deleted_by = None
        self.session.flush()

    def detail(self, lead_id):
        lead = self._get(lead_id)
        contacts = self.session.query(Contact).filter(
            Contact.tenant_id == self.principal.tenant_id,
            Contact.lead_id == lead.id, Contact.client_id.is_(None),
        ).order_by(Contact.id).all()
        fields = ('id', 'name', 'email', 'phone', 'phone_label', 'secondary_phone',
                  'secondary_phone_label', 'address', 'contact_person', 'contact_title',
                  'city', 'state', 'zip', 'notes', 'type', 'lead_status', 'lead_source')
        result = {field: getattr(lead, field) for field in fields}
        result.update(created_at=lead.created_at.isoformat() + 'Z',
                      converted_on=lead.converted_on.isoformat() + 'Z' if lead.converted_on else None,
                      contacts=[c.to_dict() for c in contacts])
        return result

    def record_view(self, lead_id):
        lead = self._get(lead_id)
        self.session.add(ActivityLog(tenant_id=self.principal.tenant_id,
            user_id=self.principal.user_id, action=ActivityType.viewed,
            entity_type='lead', entity_id=lead.id,
            description=f"Viewed lead '{lead.name}'"))

    def list_mine(self, page=1, per_page=20, sort_order="newest"):
        if type(page) is not int or type(per_page) is not int or page < 1 or not 1 <= per_page <= 200:
            raise ValueError("Invalid pagination: page must be positive and per_page between 1 and 200")
        # Validate sort order
        if sort_order not in ["newest", "oldest", "alphabetical"]:
            sort_order = "newest"

        query = self.session.query(Lead).filter(
            Lead.tenant_id == self.principal.tenant_id,
            Lead.deleted_at == None,
            or_(
                Lead.assigned_to == self.principal.user_id,
                and_(
                    Lead.assigned_to == None,
                    Lead.created_by == self.principal.user_id
                )
            )
        )

        # Apply sorting
        if sort_order == "newest":
            query = query.order_by(Lead.created_at.desc())
        elif sort_order == "oldest":
            query = query.order_by(Lead.created_at.asc())
        elif sort_order == "alphabetical":
            query = query.order_by(Lead.name.asc())

        total = query.count()
        leads = query.offset((page - 1) * per_page).limit(per_page).all()

        response = ({
            "leads": self._list_rows(leads, names=True),
            "total": total,
            "page": page,
            "per_page": per_page,
            "sort_order": sort_order
        })
        return response

    def list_all(self, page=1, per_page=20, sort_order="newest", user_email=None):
        self._require_admin()
        if type(page) is not int or type(per_page) is not int or page < 1 or not 1 <= per_page <= 200:
            raise ValueError("Invalid pagination: page must be positive and per_page between 1 and 200")
        # Get pagination parameters
        # Validate sort order
        if sort_order not in ["newest", "oldest", "alphabetical"]:
            sort_order = "newest"

        query = self.session.query(Lead).filter(
            Lead.tenant_id == self.principal.tenant_id,
            Lead.deleted_at == None
        )

        # Filter by user if specified
        if user_email:
            query = query.filter(
                or_(
                    Lead.assigned_user.has(and_(User.email == user_email, User.tenant_id == self.principal.tenant_id)),
                    Lead.created_by_user.has(and_(User.email == user_email, User.tenant_id == self.principal.tenant_id))
                )
            )

        # Apply sorting
        if sort_order == "newest":
            query = query.order_by(Lead.created_at.desc())
        elif sort_order == "oldest":
            query = query.order_by(Lead.created_at.asc())
        elif sort_order == "alphabetical":
            query = query.order_by(Lead.name.asc())

        total = query.count()
        leads = query.offset((page - 1) * per_page).limit(per_page).all()

        response_data = {
            "leads": self._list_rows(leads, names=True, creator=True),
            "total": total,
            "page": page,
            "per_page": per_page,
            "sort_order": sort_order,
            "user_email": user_email
        }

        response = (response_data)
        return response

    def list_assigned(self):
        self._require_admin()
        leads = self.session.query(Lead).filter(
            Lead.tenant_id == self.principal.tenant_id,
            Lead.deleted_at == None,
            Lead.assigned_to != None
        ).all()

        return self._list_rows(leads)

    def list_trash(self):
        if not self.principal.is_admin:
            trashed = self.session.query(Lead).filter(
                Lead.tenant_id == self.principal.tenant_id,
                Lead.deleted_at != None,
                or_(
                    Lead.created_by == self.principal.user_id,
                    Lead.assigned_to == self.principal.user_id
                )
            ).order_by(Lead.deleted_at.desc()).all()
        else:
            trashed = self.session.query(Lead).filter(
                Lead.tenant_id == self.principal.tenant_id,
                Lead.deleted_at != None
            ).order_by(Lead.deleted_at.desc()).all()

        return ([
            {
                "id": l.id,
                "name": l.name,
                "deleted_at": l.deleted_at.isoformat() + "Z",
                "deleted_by": l.deleted_by
            } for l in trashed
        ])

    def bulk_delete(self, lead_ids):
        self._require_admin()
        if not isinstance(lead_ids, list) or not lead_ids or any(type(i) is not int or i < 1 for i in lead_ids):
            raise ValueError("Positive integer lead IDs are required")
        # Soft delete only leads that belong to this tenant and haven't already been deleted
        updated_count = self.session.query(Lead).filter(
            Lead.tenant_id == self.principal.tenant_id,
            Lead.id.in_(lead_ids),
            Lead.deleted_at == None
        ).update(
            {Lead.deleted_at: datetime.utcnow(), Lead.deleted_by: self.principal.user_id},
            synchronize_session=False
        )
        return updated_count

    def bulk_purge(self, lead_ids):
        self._require_admin()
        if not isinstance(lead_ids, list) or not lead_ids or any(type(i) is not int or i < 1 for i in lead_ids):
            raise ValueError("Positive integer lead IDs are required")
        # Only purge leads that are already soft-deleted
        deleted_count = self.session.query(Lead).filter(
            Lead.tenant_id == self.principal.tenant_id,
            Lead.id.in_(lead_ids),
            Lead.deleted_at != None
        ).delete(synchronize_session=False)

        return deleted_count

    def purge(self, lead_id):
        self._require_admin()
        lead = self.session.query(Lead).filter(
            Lead.id == lead_id,
            Lead.tenant_id == self.principal.tenant_id,
            Lead.deleted_at != None
        ).first()

        if not lead:
            raise RecordNotFound("Lead not found or not eligible for purge")

        self.session.delete(lead)
        self.session.flush()

    def _require_admin(self):
        if not self.principal.is_admin:
            raise PermissionError("Administrator access required")

    def _list_rows(self, leads, *, names=False, creator=False):
        users = {}
        if (names or creator) and leads:
            user_ids = {i for lead in leads for i in (lead.assigned_to, lead.created_by) if i is not None}
            users = {u.id: u.email for u in self.session.query(User).filter(
                User.tenant_id == self.principal.tenant_id, User.id.in_(user_ids))}
        fields = ('id', 'name', 'contact_person', 'contact_title', 'email', 'phone',
                  'phone_label', 'secondary_phone', 'secondary_phone_label', 'address',
                  'city', 'state', 'zip', 'notes', 'assigned_to', 'lead_status',
                  'lead_source', 'type')
        rows = []
        for lead in leads:
            row = {field: getattr(lead, field) for field in fields}
            row['created_at'] = lead.created_at.isoformat() + 'Z'
            row['converted_on'] = lead.converted_on.isoformat() + 'Z' if lead.converted_on else None
            if names:
                row['assigned_to_name'] = users.get(lead.assigned_to) or users.get(lead.created_by)
            if creator:
                row['created_by_name'] = users.get(lead.created_by)
            rows.append(row)
        return rows
