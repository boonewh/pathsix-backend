"""Contact operations with inherited parent access and caller-owned transactions."""
from app.models import Contact, Client, Lead
from app.schemas.contacts import ContactCreateSchema, ContactUpdateSchema
from app.services.principal import Principal
from app.services.access import owned_record_filter
from app.services.database_context import bind_principal
from app.services.errors import RecordNotFound
from app.utils.phone_utils import clean_phone_number


class ContactService:
    def __init__(self, session, principal: Principal):
        if not isinstance(principal, Principal):
            raise TypeError("An authenticated principal is required")
        self.session = session
        self.principal = principal
        bind_principal(session, principal)

    def _parent(self, client_id, lead_id):
        if (client_id is not None) + (lead_id is not None) != 1:
            raise ValueError("Invalid parent combination")
        model, value = (Client, client_id) if client_id is not None else (Lead, lead_id)
        if isinstance(value, bool) or not str(value).isdigit() or int(value) < 1:
            raise ValueError("Invalid relationship ID")
        if self.session.query(model).filter(model.id == int(value),
                owned_record_filter(model, self.principal)).first() is None:
            raise RecordNotFound("Related record not found")
        return model, int(value)

    def _get(self, contact_id):
        contact = self.session.query(Contact).filter(
            Contact.id == contact_id, Contact.tenant_id == self.principal.tenant_id).first()
        if contact is None:
            raise RecordNotFound("Contact not found")
        self._parent(contact.client_id, contact.lead_id)
        return contact

    def list_for_parent(self, *, client_id=None, lead_id=None):
        if client_id is None and lead_id is None:
            return []
        model, parent_id = self._parent(client_id, lead_id)
        query = self.session.query(Contact).filter(Contact.tenant_id == self.principal.tenant_id)
        if model is Client:
            query = query.filter(Contact.client_id == parent_id, Contact.lead_id.is_(None))
        else:
            query = query.filter(Contact.lead_id == parent_id, Contact.client_id.is_(None))
        fields = ('id', 'first_name', 'last_name', 'title', 'email', 'phone', 'phone_label',
                  'secondary_phone', 'secondary_phone_label', 'notes')
        return [{field: getattr(contact, field) for field in fields}
                for contact in query.order_by(Contact.id).all()]

    @staticmethod
    def _fields(data, *, partial=False):
        fields = data.model_dump(exclude_unset=partial)
        if 'first_name' in fields and fields['first_name'] is None:
            raise ValueError("Contact first name cannot be null")
        for field in ('phone', 'secondary_phone'):
            if field in fields:
                fields[field] = clean_phone_number(fields[field]) if fields[field] else None
        if 'email' in fields:
            fields['email'] = str(fields['email']) if fields['email'] else None
        return fields

    def create(self, data: ContactCreateSchema):
        if not isinstance(data, ContactCreateSchema):
            raise TypeError("Validated contact data required")
        self._parent(data.client_id, data.lead_id)
        contact = Contact(**self._fields(data), tenant_id=self.principal.tenant_id)
        self.session.add(contact)
        self.session.flush()
        return contact.id

    def update(self, contact_id, data: ContactUpdateSchema):
        if not isinstance(data, ContactUpdateSchema):
            raise TypeError("Validated contact data required")
        contact = self._get(contact_id)
        fields = self._fields(data, partial=True)
        self._parent(fields.get('client_id', contact.client_id), fields.get('lead_id', contact.lead_id))
        for field, value in fields.items():
            setattr(contact, field, value)
        self.session.flush()

    def delete(self, contact_id):
        self.session.delete(self._get(contact_id))
        self.session.flush()
