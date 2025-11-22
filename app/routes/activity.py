from quart import Blueprint, jsonify, request
from sqlalchemy import func, desc
from app.database import SessionLocal
from app.models import ActivityLog, Client, Lead, Project, Account
from app.utils.auth_utils import requires_auth

activity_bp = Blueprint("activity", __name__, url_prefix="/api/activity")


@activity_bp.route("/recent", methods=["GET"])
@requires_auth()
async def recent_activity():
    user = request.user
    session = SessionLocal()
    try:
        limit = int(request.args.get("limit", 10))
        limit = min(limit, 50)

        # Get most recent log per entity_type + entity_id for this user
        subquery = (
            session.query(
                ActivityLog.entity_type,
                ActivityLog.entity_id,
                func.max(ActivityLog.timestamp).label("last_touched")
            )
            .filter(ActivityLog.user_id == user.id)
            .group_by(ActivityLog.entity_type, ActivityLog.entity_id)
            .subquery()
        )

        results = session.query(
            subquery.c.entity_type,
            subquery.c.entity_id,
            subquery.c.last_touched
        ).order_by(desc(subquery.c.last_touched)).limit(limit).all()

        output = []
        for row in results:
            entity_type = row.entity_type
            entity_id = row.entity_id
            last_touched = row.last_touched
            name = None
            profile_link = None

            if entity_type == "client":
                client = session.query(Client).filter(
                    Client.id == entity_id,
                    Client.tenant_id == user.tenant_id,
                    Client.deleted_at == None
                ).first()
                if client:
                    name = client.name
                    profile_link = f"/clients/{client.id}"

            elif entity_type == "lead":
                lead = session.query(Lead).filter(
                    Lead.id == entity_id,
                    Lead.tenant_id == user.tenant_id,
                    Lead.deleted_at == None
                ).first()
                if lead:
                    name = lead.name
                    profile_link = f"/leads/{lead.id}"

            # NEW: Add project support
            elif entity_type == "project":
                project = session.query(Project).filter(
                    Project.id == entity_id,
                    Project.tenant_id == user.tenant_id
                ).first()
                if project:
                    name = project.project_name
                    profile_link = f"/projects/{project.id}"

            elif entity_type == "account":
                account = session.query(Account).filter(
                    Account.id == entity_id,
                    Account.tenant_id == user.tenant_id
                ).first()
                if account and account.client and account.client.tenant_id == user.tenant_id and account.client.deleted_at is None:
                    name = account.account_name or account.account_number
                    profile_link = f"/clients/{account.client.id}"

            if name and profile_link:
                output.append({
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "name": name,
                    "last_touched": last_touched.isoformat() + "Z",
                    "profile_link": profile_link
                })

        response = jsonify(output)
        response.headers["Cache-Control"] = "no-store"
        return response

    finally:
        session.close()