"""Lead operations with explicit tenant context and caller-owned transactions.

Methods never commit. Adapters commit once after a successful operation; failures
must roll back. Detail reads are pure; web activity logging is a separate method.
"""
from datetime import datetime
from app.models import Lead, Contact, ActivityLog, ActivityType
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
