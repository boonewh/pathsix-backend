"""Client operations with explicit tenant context and caller-owned transactions.

Methods never commit. Adapters commit once after a successful operation; failures
must roll back. Detail reads are pure; web activity logging is a separate method.
"""
from datetime import datetime
from app.models import Client, Lead, Contact, ActivityLog, ActivityType
from app.schemas.clients import ClientCreateSchema, ClientUpdateSchema
from app.services.principal import Principal
from app.services.access import owned_record_filter
from app.utils.phone_utils import clean_phone_number


class RecordNotFound(Exception):
    pass


class ClientService:
    def __init__(self, session, principal: Principal):
        if not isinstance(principal, Principal):
            raise TypeError("An authenticated principal is required")
        self.session = session
        self.principal = principal
        from app.services.database_context import bind_principal
        bind_principal(session, principal)

    def _get(self, client_id, *, include_deleted=False):
        client = self.session.query(Client).filter(
            Client.id == client_id,
            owned_record_filter(Client, self.principal, include_deleted=include_deleted),
        ).first()
        if client is None:
            raise RecordNotFound("Client not found")
        return client

    def create(self, data: ClientCreateSchema):
        if not isinstance(data, ClientCreateSchema):
            raise TypeError("Validated client data required")
        if data.source_lead_id is not None:
            if data.source_lead_id < 1:
                raise ValueError("Invalid source lead ID")
            lead = self.session.query(Lead).filter(
                Lead.id == data.source_lead_id, owned_record_filter(Lead, self.principal),
            ).first()
            if lead is None:
                raise RecordNotFound("Related record not found")
        fields = data.model_dump()
        for field in ('phone', 'secondary_phone'):
            fields[field] = clean_phone_number(fields[field]) if fields[field] else None
        fields['email'] = str(data.email) if data.email else None
        client = Client(**fields, tenant_id=self.principal.tenant_id,
                        created_by=self.principal.user_id,
                        converted_on=datetime.utcnow() if data.source_lead_id else None)
        self.session.add(client)
        self.session.flush()
        return client.id

    def update(self, client_id, data: ClientUpdateSchema):
        if not isinstance(data, ClientUpdateSchema):
            raise TypeError("Validated client data required")
        client = self._get(client_id)
        fields = data.model_dump(exclude_unset=True)
        if 'name' in fields and fields['name'] is None:
            raise ValueError("Client name cannot be null")
        for field, value in fields.items():
            if field in ('phone', 'secondary_phone'):
                value = clean_phone_number(value) if value else None
            elif field == 'email':
                value = str(value) if value else None
            setattr(client, field, value)
        client.updated_by = self.principal.user_id
        client.updated_at = datetime.utcnow()
        self.session.flush()
        return client.id

    def delete(self, client_id):
        client = self._get(client_id, include_deleted=True)
        if client.deleted_at is not None:
            return False
        client.deleted_at = datetime.utcnow()
        client.deleted_by = self.principal.user_id
        self.session.flush()
        return True

    def restore(self, client_id):
        client = self._get(client_id, include_deleted=True)
        if client.deleted_at is None:
            raise RecordNotFound("Client not found or not deleted")
        client.deleted_at = None
        client.deleted_by = None
        self.session.flush()

    def detail(self, client_id):
        client = self._get(client_id)
        lead_origin = None
        if client.source_lead_id:
            lead = self.session.query(Lead).filter(
                Lead.id == client.source_lead_id, owned_record_filter(Lead, self.principal),
            ).first()
            if lead:
                lead_origin = {
                    'lead_id': lead.id, 'lead_source': lead.lead_source,
                    'lead_created_at': lead.created_at.isoformat() + 'Z',
                    'converted_on': client.converted_on.isoformat() + 'Z' if client.converted_on else None,
                    'days_in_pipeline': (client.converted_on - lead.created_at).days if client.converted_on else None,
                }
        contacts = self.session.query(Contact).filter(
            Contact.tenant_id == self.principal.tenant_id,
            Contact.client_id == client.id, Contact.lead_id.is_(None),
        ).order_by(Contact.id).all()
        fields = ('id', 'name', 'email', 'phone', 'phone_label', 'secondary_phone',
                  'secondary_phone_label', 'address', 'contact_person', 'contact_title',
                  'city', 'state', 'zip', 'notes', 'type')
        result = {field: getattr(client, field) for field in fields}
        result.update(created_at=client.created_at.isoformat() + 'Z',
                      lead_origin=lead_origin, contacts=[c.to_dict() for c in contacts])
        return result

    def record_view(self, client_id):
        client = self._get(client_id)
        self.session.add(ActivityLog(tenant_id=self.principal.tenant_id,
            user_id=self.principal.user_id, action=ActivityType.viewed,
            entity_type='client', entity_id=client.id,
            description=f"Viewed client '{client.name}'"))
