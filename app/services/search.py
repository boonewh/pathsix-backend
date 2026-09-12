"""Bounded CRM search that enforces access without relying on HTTP hooks."""
from sqlalchemy import and_, or_
from app.models import Client, Lead, Project, Account, User
from app.services.principal import Principal
from app.services.access import owned_record_filter


class SearchService:
    def __init__(self, session, principal: Principal):
        if not isinstance(principal, Principal):
            raise TypeError("An authenticated principal is required")
        self.session = session
        self.principal = principal
        from app.services.database_context import bind_principal
        bind_principal(session, principal)

    def _parent_access(self, model):
        return owned_record_filter(model, self.principal)

    def _project_access(self):
        from app.services.access import project_access_filter
        return project_access_filter(self.principal)

    def search(self, query: str, limit: int = 10):
        if not isinstance(query, str) or len(query) > 200:
            raise ValueError("Search text must be at most 200 characters")
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("Limit must be between 1 and 20 per record type")
        query = query.strip().lower()
        if not query:
            return []
        # User text is a literal substring, not a SQL wildcard expression.
        pattern = '%' + query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        contact_fields = ('name', 'contact_person', 'email', 'phone', 'address', 'city', 'state', 'zip', 'notes')
        resources = [
            (Client, 'client', contact_fields, self._parent_access(Client)),
            (Lead, 'lead', contact_fields, self._parent_access(Lead)),
            (Project, 'project', ('project_name', 'project_description', 'project_status'),
             and_(Project.deleted_at.is_(None), self._project_access())),
            (Account, 'account', ('account_name', 'account_number', 'notes'),
             Account.client.has(self._parent_access(Client))),
        ]
        if self.principal.is_admin:
            resources.append((User, 'user', ('email',), User.tenant_id == self.principal.tenant_id))
        results = []
        for model, kind, fields, access in resources:
            rows = self.session.query(model).filter(
                model.tenant_id == self.principal.tenant_id, access,
                or_(*(getattr(model, field).ilike(pattern, escape='\\') for field in fields)),
            ).order_by(model.id).limit(limit).all()
            for row in rows:
                name = (row.project_name if kind == 'project' else
                        row.account_name or row.account_number if kind == 'account' else
                        row.email if kind == 'user' else row.name)
                link = (None if kind == 'user' else f'/clients/{row.client_id}' if kind == 'account'
                        else f'/{kind}s/{row.id}')
                results.append({'type': kind, 'id': row.id, 'name': name, 'link': link,
                    'matches': [field for field in fields if query in (getattr(row, field) or '').lower()]})
        return results
