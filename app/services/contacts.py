"""Contact operations with inherited parent access and caller-owned transactions."""
from sqlalchemy import and_, case, func, or_
from app.models import Contact, Client, Lead, Project
from app.schemas.contacts import ContactCreateSchema, ContactUpdateSchema
from app.services.principal import Principal
from app.services.access import owned_record_filter, project_access_filter
from app.services.database_context import bind_principal
from app.services.errors import RecordNotFound
from app.utils.phone_utils import clean_phone_number
from app.utils.tombstones import bury


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
            Contact.id == contact_id, Contact.tenant_id == self.principal.tenant_id,
            # Primary and project-owned people are edited through their record.
            Contact.is_primary.is_(False), Contact.project_id.is_(None)).first()
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
        query = query.filter(Contact.is_primary.is_(False))
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
        bury(self._get(contact_id), self.principal.user_id)
        self.session.flush()

    def directory(self, *, page=1, per_page=50, query=''):
        """Every person the caller can see, alphabetically, with where they belong.

        A person belongs to a client, a lead or a standalone project; the record
        must be visible and not deleted. Converted leads are deleted, so their
        people appear once, under the client they moved to.
        """
        if (type(page) is not int or not 1 <= page <= 100000
                or type(per_page) is not int or not 1 <= per_page <= 200):
            raise ValueError("Invalid page")
        if not isinstance(query, str) or len(query) > 200:
            raise ValueError("Search text must be at most 200 characters")
        p = self.principal
        name = Contact.display_name
        rows = (self.session.query(Contact, Client.name, Lead.name, Project.project_name)
                .outerjoin(Client, Contact.client_id == Client.id)
                .outerjoin(Lead, Contact.lead_id == Lead.id)
                .outerjoin(Project, Contact.project_id == Project.id)
                .filter(Contact.tenant_id == p.tenant_id, or_(
                    and_(Contact.client_id.isnot(None), owned_record_filter(Client, p)),
                    and_(Contact.lead_id.isnot(None), owned_record_filter(Lead, p)),
                    and_(Contact.project_id.isnot(None), Project.deleted_at.is_(None),
                         project_access_filter(p)))))
        text = query.strip().lower()
        if text:
            # User text is a literal substring, not a SQL wildcard expression.
            pattern = '%' + text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
            rows = rows.filter(or_(*(column.ilike(pattern, escape='\\') for column in (
                name, Contact.email, Contact.phone, Contact.title, Client.name, Lead.name, Project.project_name))))
        total = rows.count()
        # Named people first, alphabetically; people known only by email follow.
        records = rows.order_by(case((name.is_(None), 1), else_=0), func.lower(name),
                                func.lower(Contact.email), Contact.id
                                ).offset((page - 1) * per_page).limit(per_page).all()
        contacts = []
        for contact, client_name, lead_name, project_name in records:
            if contact.client_id is not None:
                parent = {'type': 'client', 'id': contact.client_id, 'name': client_name}
            elif contact.lead_id is not None:
                parent = {'type': 'lead', 'id': contact.lead_id, 'name': lead_name}
            else:
                parent = {'type': 'project', 'id': contact.project_id, 'name': project_name}
            contacts.append({'id': contact.id, 'name': contact.display_name, 'title': contact.title,
                             'email': contact.email, 'phone': contact.phone, 'phone_label': contact.phone_label,
                             'is_primary': contact.is_primary, 'parent': parent})
        return {'contacts': contacts, 'total': total, 'page': page, 'per_page': per_page}
