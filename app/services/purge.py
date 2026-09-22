"""Permanent deletion with tenant-scoped, actionable dependency conflicts.

The caller owns commit/rollback. Never detach or cascade linked records. Chat is
deliberately outside runtime read permissions; its FK remains the final guard.
"""
from sqlalchemy import func

from app.models import Account, Client, Contact, Interaction, Lead, Project, Subscription
from app.services.base import TenantService
from app.services.errors import RecordNotFound


MODELS = {"clients": Client, "leads": Lead, "projects": Project}
DEPENDENCIES = {
    "clients": ((Interaction, "client_id", "interactions"),
                (Contact, "client_id", "contacts"),
                (Project, "client_id", "projects"),
                (Account, "client_id", "account records"),
                (Subscription, "client_id", "subscriptions")),
    "leads": ((Interaction, "lead_id", "interactions"),
              (Contact, "lead_id", "contacts"),
              (Project, "lead_id", "projects"),
              (Client, "source_lead_id", "converted accounts")),
    "projects": ((Interaction, "project_id", "interactions"),),
}


class PurgeConflict(Exception):
    def __init__(self, blocked):
        super().__init__("Linked records prevent permanent deletion. No selected records were deleted.")
        self.blocked = blocked

    def payload(self):
        return {"code": "purge_blocked", "error": str(self), "blocked": self.blocked,
                "deleted_ids": []}


class PurgeService(TenantService):
    def purge(self, resource, ids, *, single=False):
        self._require_admin()
        self._ids(ids)
        model = MODELS[resource]
        # Consistent order prevents overlapping batches from taking parent locks
        # in opposite orders. PostgreSQL FK inserts wait for these row locks.
        records = self._query(model).filter(
            model.id.in_(ids), model.deleted_at.isnot(None)
        ).order_by(model.id).with_for_update().all()
        if single and not records:
            raise RecordNotFound("Record not found or not eligible for permanent deletion")
        selected_ids = [record.id for record in records]
        if not selected_ids:
            return {"message": "No eligible records were deleted", "deleted_ids": []}

        blockers = {record.id: [] for record in records}
        for child, field, label in DEPENDENCIES[resource]:
            parent_id = getattr(child, field)
            # Include soft-deleted children: they still hold a real FK reference.
            counts = self.session.query(parent_id, func.count(child.id)).filter(
                child.tenant_id == self.principal.tenant_id,
                parent_id.in_(selected_ids),
            ).group_by(parent_id).all()
            for record_id, count in counts:
                blockers[record_id].append({"kind": label, "count": count})
        blocked = [
            {"id": record.id,
             "name": record.project_name if resource == "projects" else record.name,
             "dependencies": blockers[record.id]}
            for record in records if blockers[record.id]
        ]
        if blocked:
            raise PurgeConflict(blocked)

        from app.utils.sales_audit import log_bulk_deletion
        log_bulk_deletion(self.session, self._query(model).filter(model.id.in_(selected_ids)))

        # Bulk SQL intentionally avoids ORM nulling of nullable child references.
        self._query(model).filter(model.id.in_(selected_ids), model.deleted_at.isnot(None)).delete(
            synchronize_session=False
        )
        self.session.flush()
        return {"message": f"{len(selected_ids)} record(s) permanently deleted",
                "deleted_ids": selected_ids}


def database_conflict_payload(exc):
    """Safe fallback for races, restricted chat, and legacy/cross-tenant links.

Never parse or expose driver DETAIL text, record IDs, or SQL parameters.
"""
    constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", "") or ""
    labels = {"interactions": "interactions", "contacts": "contacts",
              "projects": "projects", "accounts": "account records",
              "subscriptions": "subscriptions", "chat_messages": "messages",
              "clients": "converted accounts"}
    label = next((value for table, value in labels.items()
                  if constraint in {f"{table}_{field}_fkey" for field in
                                    ("client_id", "lead_id", "project_id", "source_lead_id")}),
                 "records")
    return {"code": "purge_blocked", "deleted_ids": [], "blocked": [],
            "error": f"Linked {label} prevent permanent deletion. No selected records were deleted. "
                     "Restore the affected record to review its links. If the links are not visible, contact an administrator."}
