"""Recent activity rechecks current record access; logs never grant permission."""

from sqlalchemy import func, desc
from app.models import ActivityLog, Client, Lead, Project, Account
from app.services.base import TenantService
from app.services.access import owned_record_filter, project_access_filter


class ActivityService(TenantService):
    def recent(self, limit=10):
        if type(limit) is not int or limit < 1:
            raise ValueError("Limit must be a positive integer")
        limit = min(limit, 50)
        # Get most recent log per entity_type + entity_id for this user
        subquery = (
            self._query(
                ActivityLog.entity_type,
                ActivityLog.entity_id,
                func.max(ActivityLog.timestamp).label("last_touched"),
            )
            .filter(
                ActivityLog.user_id == self.principal.user_id,
                ActivityLog.tenant_id == self.principal.tenant_id,
            )
            .group_by(ActivityLog.entity_type, ActivityLog.entity_id)
            .subquery()
        )

        results = (
            self._query(
                subquery.c.entity_type, subquery.c.entity_id, subquery.c.last_touched
            )
            .order_by(desc(subquery.c.last_touched))
            .limit(limit)
            .all()
        )

        # Collect entity IDs by type for bulk loading
        client_ids = []
        lead_ids = []
        project_ids = []
        account_ids = []

        for row in results:
            if row.entity_type == "client":
                client_ids.append(row.entity_id)
            elif row.entity_type == "lead":
                lead_ids.append(row.entity_id)
            elif row.entity_type == "project":
                project_ids.append(row.entity_id)
            elif row.entity_type == "account":
                account_ids.append(row.entity_id)

        # Bulk load all entities (prevents N+1)
        clients_map = {}
        if client_ids:
            clients = (
                self._query(Client)
                .filter(
                    Client.id.in_(client_ids),
                    Client.tenant_id == self.principal.tenant_id,
                    owned_record_filter(Client, self.principal),
                )
                .all()
            )
            clients_map = {c.id: c for c in clients}

        leads_map = {}
        if lead_ids:
            leads = (
                self._query(Lead)
                .filter(
                    Lead.id.in_(lead_ids),
                    Lead.tenant_id == self.principal.tenant_id,
                    owned_record_filter(Lead, self.principal),
                )
                .all()
            )
            leads_map = {l.id: l for l in leads}

        projects_map = {}
        if project_ids:
            projects = (
                self._query(Project)
                .filter(
                    Project.id.in_(project_ids),
                    Project.tenant_id == self.principal.tenant_id,
                    Project.deleted_at.is_(None),
                    project_access_filter(self.principal),
                )
                .all()
            )
            projects_map = {p.id: p for p in projects}

        accounts_map = {}
        if account_ids:
            accounts = (
                self._query(Account)
                .filter(
                    Account.id.in_(account_ids),
                    Account.tenant_id == self.principal.tenant_id,
                    Account.client.has(owned_record_filter(Client, self.principal)),
                )
                .all()
            )
            accounts_map = {a.id: a for a in accounts}

        # Build output using cached entities
        output = []
        for row in results:
            entity_type = row.entity_type
            entity_id = row.entity_id
            last_touched = row.last_touched
            name = None
            profile_link = None

            if entity_type == "client":
                client = clients_map.get(entity_id)
                if client:
                    name = client.name
                    profile_link = f"/clients/{client.id}"

            elif entity_type == "lead":
                lead = leads_map.get(entity_id)
                if lead:
                    name = lead.name
                    profile_link = f"/leads/{lead.id}"

            elif entity_type == "project":
                project = projects_map.get(entity_id)
                if project:
                    name = project.project_name
                    profile_link = f"/projects/{project.id}"

            elif entity_type == "account":
                account = accounts_map.get(entity_id)
                if (
                    account
                    and account.client
                    and account.client.tenant_id == self.principal.tenant_id
                    and account.client.deleted_at is None
                ):
                    name = account.account_name or account.account_number
                    profile_link = f"/clients/{account.client.id}"

            if name and profile_link:
                output.append(
                    {
                        "entity_type": entity_type,
                        "entity_id": entity_id,
                        "name": name,
                        "last_touched": last_touched.isoformat() + "Z",
                        "profile_link": profile_link,
                    }
                )
        return output
