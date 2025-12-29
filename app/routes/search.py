from quart import Blueprint, request, jsonify
from sqlalchemy import or_, and_, func
from app.database import SessionLocal
from app.models import Client, Lead, Project, Account, User
from app.utils.auth_utils import requires_auth

search_bp = Blueprint("search", __name__, url_prefix="/api/search")

@search_bp.route("", methods=["GET"])
@search_bp.route("/", methods=["GET"])
@requires_auth()
async def global_search():
    user = request.user
    query = request.args.get("q", "").strip().lower()
    if not query:
        return jsonify([])

    session = SessionLocal()
    try:
        results = []
        is_admin = any(role.name == "admin" for role in user.roles)

        def matched_fields(instance, fields):
            matches = []
            for field in fields:
                value = getattr(instance, field, "")
                if value and query in value.lower():
                    matches.append(field)
            return matches

        # Clients
        client_fields = ["name", "contact_person", "email", "phone", "address", "city", "state", "zip", "notes"]
        client_q = session.query(Client).filter(
            Client.tenant_id == user.tenant_id,
            Client.deleted_at == None,
            or_(*[func.lower(getattr(Client, f)).ilike(f"%{query}%") for f in client_fields])
        )
        if not is_admin:
            client_q = client_q.filter(Client.created_by == user.id)

        for c in client_q.limit(10):
            results.append({
                "type": "client",
                "id": c.id,
                "name": c.name,
                "link": f"/clients/{c.id}",
                "matches": matched_fields(c, client_fields)
            })

        # Leads
        lead_fields = ["name", "contact_person", "email", "phone", "address", "city", "state", "zip", "notes"]
        lead_q = session.query(Lead).filter(
            Lead.tenant_id == user.tenant_id,
            Lead.deleted_at == None,
            or_(*[func.lower(getattr(Lead, f)).ilike(f"%{query}%") for f in lead_fields])
        )
        if not is_admin:
            lead_q = lead_q.filter(or_(Lead.created_by == user.id, Lead.assigned_to == user.id))

        for l in lead_q.limit(10):
            results.append({
                "type": "lead",
                "id": l.id,
                "name": l.name,
                "link": f"/leads/{l.id}",
                "matches": matched_fields(l, lead_fields)
            })

        # Projects
        project_fields = ["project_name", "project_description", "project_status"]
        project_q = session.query(Project).filter(
            Project.tenant_id == user.tenant_id,
            or_(*[func.lower(getattr(Project, f)).ilike(f"%{query}%") for f in project_fields])
        )
        if not is_admin:
            project_q = project_q.filter(Project.created_by == user.id)

        for p in project_q.limit(10):
            link = f"/clients/{p.client_id}" if p.client_id else f"/leads/{p.lead_id}"
            results.append({
                "type": "project",
                "id": p.id,
                "name": p.project_name,
                "link": link,
                "matches": matched_fields(p, project_fields)
            })

        # Accounts
        account_fields = ["account_name", "account_number", "notes"]
        account_q = session.query(Account).filter(
            Account.tenant_id == user.tenant_id,
            or_(*[func.lower(getattr(Account, f)).ilike(f"%{query}%") for f in account_fields])
        )
        if not is_admin:
            account_q = account_q.join(Client).filter(Client.created_by == user.id)

        for a in account_q.limit(10):
            results.append({
                "type": "account",
                "id": a.id,
                "name": a.account_name or a.account_number,
                "link": f"/clients/{a.client_id}",
                "matches": matched_fields(a, account_fields)
            })

        # Users (admin only)
        if is_admin:
            user_fields = ["email"]
            user_q = session.query(User).filter(
                User.tenant_id == user.tenant_id,
                or_(*[func.lower(getattr(User, f)).ilike(f"%{query}%") for f in user_fields])
            )

            for u in user_q.limit(10):
                results.append({
                    "type": "user",
                    "id": u.id,
                    "name": u.email,
                    "link": None,
                    "matches": matched_fields(u, user_fields)
                })

        return jsonify(results)

    finally:
        session.close()
