"""Client operations with explicit tenant context and caller-owned transactions.

Methods never commit. Adapters commit once after a successful operation; failures
must roll back. Detail reads are pure; web activity logging is a separate method.
"""
from datetime import datetime, timedelta
from sqlalchemy import and_, or_, func
from app.models import Client, Lead, Contact, Interaction, User, ActivityLog, ActivityType
from app.schemas.clients import ClientCreateSchema, ClientUpdateSchema, ClientAssignSchema
from app.services.base import TenantService
from app.services.access import owned_record_filter
from app.utils.phone_utils import clean_phone_number
from app.services.errors import RecordNotFound


class ClientService(TenantService):
    def summaries(self, *, after_id=0, limit=20, client_id=None):
        """Bounded projection shared with delegated reads; no contacts or notes."""
        if (type(after_id) is not int or not 0 <= after_id <= 2147483647
                or type(limit) is not int or not 1 <= limit <= 50
                or (client_id is not None and (type(client_id) is not int or not 1 <= client_id <= 2147483647))):
            raise ValueError('Invalid client selection')
        query = self._query(Client).filter(owned_record_filter(Client, self.principal))
        if client_id is not None:
            query = query.filter(Client.id == client_id)
        else:
            query = query.filter(Client.id > after_id)
        # Truncate at the database boundary, before constructing a response.
        rows = query.with_entities(Client.id, func.substr(Client.name,1,200).label('name'),
            func.substr(Client.city,1,100).label('city'), func.substr(Client.state,1,50).label('state'),
            func.substr(Client.type,1,100).label('type')).order_by(Client.id).limit(limit+1).all()
        if client_id is not None and not rows:
            raise RecordNotFound('Client not found')
        return {'clients':[dict(row._mapping) for row in rows[:limit]],
                'next_after_id':rows[limit-1].id if len(rows)>limit else None}

    def _get(self, client_id, *, include_deleted=False):
        client = self._query(Client).filter(
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
            lead = self._query(Lead).filter(
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
            lead = self._query(Lead).filter(
                Lead.id == client.source_lead_id, owned_record_filter(Lead, self.principal),
            ).first()
            if lead:
                lead_origin = {
                    'lead_id': lead.id, 'lead_source': lead.lead_source,
                    'lead_created_at': lead.created_at.isoformat() + 'Z',
                    'converted_on': client.converted_on.isoformat() + 'Z' if client.converted_on else None,
                    'days_in_pipeline': (client.converted_on - lead.created_at).days if client.converted_on else None,
                }
        contacts = self._query(Contact).filter(
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

    def list_mine(self, page=1, per_page=20, sort_order="newest", activity_filter="all"):
        # Preserve My Clients: assignment takes precedence over creation, even for admins.
        query = self._query(Client).filter(
            Client.deleted_at.is_(None),
            or_(Client.assigned_to == self.principal.user_id,
                and_(Client.assigned_to.is_(None), Client.created_by == self.principal.user_id)),
        )
        return self._list_page(query, page, per_page, sort_order, activity_filter)

    def list_all(self, page=1, per_page=20, sort_order="newest", user_email=None,
                 activity_filter="all"):
        self._require_admin()
        query = self._query(Client).filter(Client.deleted_at.is_(None))
        if user_email:
            users = self._query(User.id).filter(User.email == user_email)
            query = query.filter(or_(Client.assigned_to.in_(users), Client.created_by.in_(users)))
        result = self._list_page(query, page, per_page, sort_order, activity_filter, admin=True)
        result["user_email"] = user_email
        return result

    def _list_page(self, query, page, per_page, sort_order, activity_filter, *, admin=False):
        self._pagination(page, per_page)
        if sort_order not in ("newest", "oldest", "alphabetical", "activity"):
            sort_order = "newest"
        # Aggregate once, before filtering/pagination. Avoid duplicate joins when
        # activity filtering and activity sorting are used together. Explicit tenant
        # and parent predicates also protect direct service calls without HTTP/RLS.
        stats = self._query(
            Interaction.client_id,
            func.count(Interaction.id).label("interaction_count"),
            func.max(Interaction.contact_date).label("last_interaction_date"),
        ).filter(
            Interaction.client_id.is_not(None),
            Interaction.lead_id.is_(None), Interaction.project_id.is_(None),
        ).group_by(Interaction.client_id).subquery()
        query = query.outerjoin(stats, Client.id == stats.c.client_id)
        now = datetime.utcnow()
        if activity_filter == "active":
            query = query.filter(stats.c.last_interaction_date >= now - timedelta(days=30))
        elif activity_filter == "inactive":
            query = query.filter(or_(stats.c.last_interaction_date.is_(None),
                                     stats.c.last_interaction_date < now - timedelta(days=90)))
        elif activity_filter == "new":
            query = query.filter(Client.created_at >= now - timedelta(days=7))
        ordering = {
            "newest": Client.created_at.desc(),
            "oldest": Client.created_at.asc(),
            "alphabetical": Client.name.asc(),
            "activity": stats.c.last_interaction_date.desc().nullslast(),
        }
        total = query.count()
        records = query.add_columns(stats.c.interaction_count, stats.c.last_interaction_date).order_by(
            ordering[sort_order], Client.id.asc(),
        ).offset((page - 1) * per_page).limit(per_page).all()
        rows = self._list_rows([record[0] for record in records], mode="all" if admin else "mine")
        for row, (_, count, last_date) in zip(rows, records):
            row["interaction_count"] = count or 0
            row["last_interaction_date"] = last_date.isoformat() + "Z" if last_date else None
        return {"clients": rows, "total": total, "page": page, "per_page": per_page,
                "sort_order": sort_order, "activity_filter": activity_filter}

    def _list_rows(self, clients, *, mode):
        user_ids = {i for client in clients for i in (client.assigned_to, client.created_by) if i is not None}
        users = {u.id: u.email for u in self._query(User).filter(User.id.in_(user_ids))} if user_ids else {}
        fields = ("id", "name", "email", "phone", "phone_label", "secondary_phone",
                  "secondary_phone_label", "contact_person", "contact_title", "type")
        rows = []
        for client in clients:
            row = {field: getattr(client, field) for field in fields}
            row["assigned_to_name"] = users.get(client.assigned_to)
            if mode != "assigned":
                row["assigned_to_name"] = row["assigned_to_name"] or users.get(client.created_by)
                row["created_at"] = client.created_at.isoformat() + "Z" if client.created_at else None
            if mode == "mine":
                row.update({field: getattr(client, field) for field in
                            ("address", "city", "state", "zip", "notes", "assigned_to")})
            elif mode == "all":
                row.update(created_by=client.created_by, created_by_name=users.get(client.created_by))
            rows.append(row)
        return rows

    def list_assigned(self):
        clients = self._query(Client).filter(
            Client.assigned_to == self.principal.user_id, Client.deleted_at.is_(None),
        ).order_by(Client.id).all()
        return self._list_rows(clients, mode="assigned")

    def list_trash(self):
        clients = self._query(Client).filter(
            owned_record_filter(Client, self.principal, include_deleted=True),
            Client.deleted_at.is_not(None),
        ).order_by(Client.deleted_at.desc(), Client.id).all()
        return [{"id": client.id, "name": client.name,
                 "deleted_at": client.deleted_at.isoformat() + "Z", "deleted_by": client.deleted_by}
                for client in clients]

    def assign(self, client_id, data: ClientAssignSchema):
        """Flush the assignment; the adapter commits before sending notification."""
        self._require_admin()
        if not isinstance(data, ClientAssignSchema):
            raise TypeError("Validated assignment data required")
        client = self._get(client_id)
        assigned_user = self._query(User).filter(
            User.id == data.assigned_to, User.is_active.is_(True),
        ).first()
        if assigned_user is None:
            raise ValueError("Assigned user not found or inactive")
        client.assigned_to = assigned_user.id
        client.updated_by = self.principal.user_id
        client.updated_at = datetime.utcnow()
        self.session.flush()
        return {"to_email": assigned_user.email, "entity_type": "client", "entity_name": client.name}

    def bulk_delete(self, client_ids):
        self._require_admin()
        self._ids(client_ids)
        clients = self._query(Client).filter(
            Client.id.in_(client_ids), Client.deleted_at.is_(None),
        ).all()
        now = datetime.utcnow()
        for client in clients:
            client.deleted_at = now
            client.deleted_by = self.principal.user_id
        # ORM events record one transactional deletion per changed client.
        self.session.flush()
        return len(clients)
