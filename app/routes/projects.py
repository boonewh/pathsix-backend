from quart import Blueprint, request, jsonify
from datetime import datetime
from app.models import Project, ActivityLog, ActivityType, Client, Lead, User
from app.database import SessionLocal
from app.utils.auth_utils import requires_auth
from app.utils.phone_utils import clean_phone_number  
from app.utils.email_utils import send_assignment_notification
from app.constants import PROJECT_STATUS_OPTIONS, PHONE_LABELS
from sqlalchemy.orm import joinedload
from sqlalchemy import or_, and_

projects_bp = Blueprint("projects", __name__, url_prefix="/api/projects")

def parse_date_with_default_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        try:
            return datetime.fromisoformat(value + "T00:00:00")
        except ValueError:
            return None



@projects_bp.route("/", methods=["GET"])
@requires_auth()
async def list_projects():
    user = request.user
    session = SessionLocal()
    try:
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 20))
        sort_order = request.args.get("sort", "newest")

        if sort_order not in ["newest", "oldest", "alphabetical"]:
            sort_order = "newest"

        query = session.query(Project).options(
            joinedload(Project.client),
            joinedload(Project.lead)
        ).filter(
            Project.tenant_id == user.tenant_id
        ).filter(
            or_(
                # Projects tied to clients assigned to or created by the user
                and_(
                    Project.client_id != None,
                    or_(
                        Project.client.has(Client.assigned_to == user.id),
                        Project.client.has(Client.created_by == user.id)
                    )
                ),
                # Projects tied to leads assigned to or created by the user
                and_(
                    Project.lead_id != None,
                    or_(
                        Project.lead.has(Lead.assigned_to == user.id),
                        Project.lead.has(Lead.created_by == user.id)
                    )
                ),
                # Projects with no client or lead but created by the user
                and_(
                    Project.client_id == None,
                    Project.lead_id == None,
                    Project.created_by == user.id
                )
            )
        )

        # Sorting
        if sort_order == "newest":
            query = query.order_by(Project.created_at.desc())
        elif sort_order == "oldest":
            query = query.order_by(Project.created_at.asc())
        elif sort_order == "alphabetical":
            query = query.order_by(Project.project_name.asc())

        total = query.count()
        projects = query.offset((page - 1) * per_page).limit(per_page).all()

        response = jsonify({
            "projects": [
                {
                    "id": p.id,
                    "project_name": p.project_name,
                    "type": p.type,
                    "project_status": p.project_status,
                    "project_description": p.project_description,
                    "notes": p.notes,
                    "project_start": p.project_start.isoformat() if p.project_start else None,
                    "project_end": p.project_end.isoformat() if p.project_end else None,
                    "project_worth": p.project_worth,
                    "client_id": p.client_id,
                    "lead_id": p.lead_id,
                    "client_name": p.client.name if p.client else None,
                    "lead_name": p.lead.name if p.lead else None,
                    "created_at": p.created_at.isoformat() if p.created_at else None,
                    "primary_contact_name": p.primary_contact_name,
                    "primary_contact_title": p.primary_contact_title,
                    "primary_contact_email": p.primary_contact_email,
                    "primary_contact_phone": p.primary_contact_phone,
                    "primary_contact_phone_label": p.primary_contact_phone_label
                } for p in projects
            ],
            "total": total,
            "page": page,
            "per_page": per_page,
            "sort_order": sort_order
        })
        response.headers["Cache-Control"] = "no-store"
        return response
    finally:
        session.close()




@projects_bp.route("/<int:project_id>", methods=["GET"])
@requires_auth()
async def get_project(project_id):
    user = request.user
    session = SessionLocal()
    try:
        project = session.query(Project).options(
            joinedload(Project.client),
            joinedload(Project.lead)
        ).filter(
            Project.id == project_id,
            Project.tenant_id == user.tenant_id
        ).first()

        if not project:
            return jsonify({"error": "Project not found"}), 404

        # 🆕 Add activity log for "Recently Touched"
        log = ActivityLog(
            tenant_id=user.tenant_id,
            user_id=user.id,
            action=ActivityType.viewed,
            entity_type="project",
            entity_id=project.id,
            description=f"Viewed project '{project.project_name}'"
        )
        session.add(log)
        session.commit()

        return jsonify({
            "id": project.id,
            "project_name": project.project_name,
            "type": project.type,
            "project_status": project.project_status,
            "project_description": project.project_description,
            "notes": project.notes,
            "project_start": project.project_start.isoformat() + "Z" if project.project_start else None,
            "project_end": project.project_end.isoformat() + "Z" if project.project_end else None,
            "project_worth": project.project_worth,
            "client_id": project.client_id,
            "lead_id": project.lead_id,
            "client_name": project.client.name if project.client else None,
            "lead_name": project.lead.name if project.lead else None,
            "created_by": project.created_by,
            "created_at": project.created_at.isoformat() + "Z" if project.created_at else None,
            "primary_contact_name": getattr(project, 'primary_contact_name', None),
            "primary_contact_title": getattr(project, 'primary_contact_title', None),
            "primary_contact_email": getattr(project, 'primary_contact_email', None),
            "primary_contact_phone": getattr(project, 'primary_contact_phone', None),
            "primary_contact_phone_label": getattr(project, 'primary_contact_phone_label', None)
        })
    finally:
        session.close()


@projects_bp.route("/", methods=["POST"])
@requires_auth()
async def create_project():
    user = request.user
    data = await request.get_json()
    session = SessionLocal()

    try:
        status = data.get("project_status", PROJECT_STATUS_OPTIONS[0])
        if status not in PROJECT_STATUS_OPTIONS:
            status = PROJECT_STATUS_OPTIONS[0]

        project = Project(
            tenant_id=user.tenant_id,
            client_id=data.get("client_id"),
            lead_id=data.get("lead_id"),
            project_name=data["project_name"],
            type=data.get("type", "None"),
            project_status=status,
            project_description=data.get("project_description"),
            notes=data.get("notes"),
            project_start=parse_date_with_default_time(data.get("project_start")),
            project_end=parse_date_with_default_time(data.get("project_end")),
            project_worth=data.get("project_worth") or 0,
            created_by=user.id,
            created_at=datetime.utcnow(),
            # NEW: Handle contact fields
            primary_contact_name=data.get("primary_contact_name"),
            primary_contact_title=data.get("primary_contact_title"),
            primary_contact_email=data.get("primary_contact_email"),
            primary_contact_phone=clean_phone_number(data.get("primary_contact_phone")) if data.get("primary_contact_phone") else None,
            primary_contact_phone_label=data.get("primary_contact_phone_label", PHONE_LABELS[0])
        )
        session.add(project)
        session.commit()
        session.refresh(project)

        return jsonify({
            "id": project.id,
            "project_name": project.project_name,
            "type": project.type,
            "project_status": project.project_status,
            "client_name": project.client.name if project.client else None,
            "lead_name": project.lead.name if project.lead else None,
        }), 201
    finally:
        session.close()

@projects_bp.route("/<int:project_id>", methods=["PUT"])
@requires_auth()
async def update_project(project_id):
    user = request.user
    data = await request.get_json()
    session = SessionLocal()
    try:
        project = session.query(Project).filter(
            Project.id == project_id,
            Project.tenant_id == user.tenant_id
        ).first()

        if not project:
            return jsonify({"error": "Project not found"}), 404

        # Capture current assigned_to
        previous_assigned_to = getattr(project, "assigned_to", None)

        # Update basic fields
        for field in [
            "project_name", "type", "project_description", "project_worth", "client_id", "lead_id", "notes", "assigned_to"
        ]:
            if field in data:
                if field == "project_worth":
                    setattr(project, field, data.get("project_worth") or 0)
                else:
                    setattr(project, field, data[field])

        # NEW: Handle contact fields
        contact_fields = [
            "primary_contact_name", "primary_contact_title", "primary_contact_email", "primary_contact_phone_label"
        ]
        for field in contact_fields:
            if field in data:
                setattr(project, field, data[field] or None)

        # Handle phone number with cleaning
        if "primary_contact_phone" in data:
            project.primary_contact_phone = clean_phone_number(data["primary_contact_phone"]) if data["primary_contact_phone"] else None
                
        if "project_status" in data:
            status = data["project_status"]
            if status in PROJECT_STATUS_OPTIONS:
                project.project_status = status

        if "project_start" in data:
            project.project_start = parse_date_with_default_time(data["project_start"])
        if "project_end" in data:
            project.project_end = parse_date_with_default_time(data["project_end"])

        session.commit()
        session.refresh(project)

        if "assigned_to" in data and data["assigned_to"] and data["assigned_to"] != previous_assigned_to:
            assigned_user = session.query(User).get(data["assigned_to"])
            if assigned_user and assigned_user.email:
                await send_assignment_notification(
                    to_email=assigned_user.email,
                    entity_type="project",
                    entity_name=project.project_name,
                    assigned_by=user.email
                )


        return jsonify({
            "id": project.id,
            "project_name": project.project_name,
            "type": project.type,
            "project_status": project.project_status,
            "client_name": project.client.name if project.client else None,
            "lead_name": project.lead.name if project.lead else None,
        })
    finally:
        session.close()


@projects_bp.route("/<int:project_id>/interactions", methods=["GET"])
@requires_auth()
async def get_project_interactions(project_id):
    """Get interactions for a specific project"""
    user = request.user
    session = SessionLocal()
    try:
        # Verify project exists and user has access
        project = session.query(Project).filter(
            Project.id == project_id,
            Project.tenant_id == user.tenant_id
        ).first()

        if not project:
            return jsonify({"error": "Project not found"}), 404

        # Check access permissions
        if not any(role.name == "admin" for role in user.roles):
            if project.created_by != user.id:
                return jsonify({"error": "Access denied"}), 403

        # This will redirect to the main interactions endpoint with project_id filter
        # The frontend can call /api/interactions/?project_id={project_id} directly
        return jsonify({
            "redirect": f"/api/interactions/?project_id={project_id}",
            "message": "Use the main interactions endpoint with project_id parameter"
        })
    finally:
        session.close()

@projects_bp.route("/all", methods=["GET"])
@requires_auth(roles=["admin"])
async def list_all_projects():
    user = request.user
    session = SessionLocal()
    try:
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 20))
        sort_order = request.args.get("sort", "newest")
        user_email = request.args.get("user_email")
        
        if sort_order not in ["newest", "oldest", "alphabetical"]:
            sort_order = "newest"

        query = session.query(Project).options(
            joinedload(Project.client).joinedload(Client.assigned_user),
            joinedload(Project.client).joinedload(Client.created_by_user),
            joinedload(Project.lead).joinedload(Lead.assigned_user),
            joinedload(Project.lead).joinedload(Lead.created_by_user),
        ).filter(
            Project.tenant_id == user.tenant_id
        )

        if user_email:
            subquery_user_id = session.query(User.id).filter(User.email == user_email).scalar_subquery()
            query = query.filter(
                or_(
                    # Client projects
                    and_(
                        Project.client_id != None,
                        or_(
                            Project.client.has(Client.assigned_user.has(User.email == user_email)),
                            Project.client.has(Client.created_by_user.has(User.email == user_email))
                        )
                    ),
                    # Lead projects
                    and_(
                        Project.lead_id != None,
                        or_(
                            Project.lead.has(Lead.assigned_user.has(User.email == user_email)),
                            Project.lead.has(Lead.created_by_user.has(User.email == user_email))
                        )
                    ),
                    # ✅ Unattached projects created by this user
                    and_(
                        Project.client_id == None,
                        Project.lead_id == None,
                        Project.created_by == subquery_user_id
                    )
                )
            )

        if sort_order == "newest":
            query = query.order_by(Project.created_at.desc())
        elif sort_order == "oldest":
            query = query.order_by(Project.created_at.asc())
        elif sort_order == "alphabetical":
            query = query.order_by(Project.project_name.asc())

        total = query.count()
        projects = query.offset((page - 1) * per_page).limit(per_page).all()

        response_data = {
            "projects": []
        }

        for p in projects:
            assigned_to_email = None
            if p.client and p.client.assigned_user:
                assigned_to_email = p.client.assigned_user.email
            elif p.client and p.client.created_by_user:
                assigned_to_email = p.client.created_by_user.email
            elif p.lead and p.lead.assigned_user:
                assigned_to_email = p.lead.assigned_user.email
            elif p.lead and p.lead.created_by_user:
                assigned_to_email = p.lead.created_by_user.email

            response_data["projects"].append({
                "id": p.id,
                "project_name": p.project_name,
                "type": p.type,
                "project_status": p.project_status,
                "project_description": p.project_description,
                "notes": p.notes,
                "project_start": p.project_start.isoformat() if p.project_start else None,
                "project_end": p.project_end.isoformat() if p.project_end else None,
                "project_worth": p.project_worth,
                "client_id": p.client_id,
                "lead_id": p.lead_id,
                "client_name": p.client.name if p.client else None,
                "lead_name": p.lead.name if p.lead else None,
                "assigned_to_email": assigned_to_email,
                "created_at": p.created_at.isoformat() if p.created_at else None,
                # NEW: Include contact fields in admin view
                "primary_contact_name": p.primary_contact_name,
                "primary_contact_title": p.primary_contact_title,
                "primary_contact_email": p.primary_contact_email,
                "primary_contact_phone": p.primary_contact_phone,
                "primary_contact_phone_label": p.primary_contact_phone_label
            })

        response_data.update({
            "total": total,
            "page": page,
            "per_page": per_page,
            "sort_order": sort_order,
            "user_email": user_email
        })

        response = jsonify(response_data)
        response.headers["Cache-Control"] = "no-store"
        return response
    finally:
        session.close()

@projects_bp.route("/by-client/<int:client_id>", methods=["GET"])
@requires_auth()
async def list_projects_by_client(client_id):
    user = request.user
    session = SessionLocal()
    try:
        client = session.query(Client).filter(
            Client.id == client_id,
            Client.tenant_id == user.tenant_id,
            Client.deleted_at == None,
        ).first()

        if not client:
            return jsonify({"error": "Client not found"}), 404

        if not any(role.name == "admin" for role in user.roles):
            if client.assigned_to != user.id and client.created_by != user.id:
                return jsonify({"error": "Forbidden"}), 403

        projects = session.query(Project).filter(
            Project.client_id == client_id,
            Project.tenant_id == user.tenant_id
        ).order_by(Project.created_at.desc()).all()

        return jsonify([
            {
                "id": p.id,
                "type": p.type,
                "project_name": p.project_name,
                "project_status": p.project_status,
                "project_description": p.project_description,
                "notes": p.notes,
                "project_start": p.project_start.isoformat() if p.project_start else None,
                "project_end": p.project_end.isoformat() if p.project_end else None,
                "project_worth": p.project_worth,
                "created_at": p.created_at.isoformat() if p.created_at else None,
                # NEW: Include contact fields
                "primary_contact_name": p.primary_contact_name,
                "primary_contact_title": p.primary_contact_title,
                "primary_contact_email": p.primary_contact_email,
                "primary_contact_phone": p.primary_contact_phone,
                "primary_contact_phone_label": p.primary_contact_phone_label
            } for p in projects
        ])
    finally:
        session.close()

@projects_bp.route("/by-lead/<int:lead_id>", methods=["GET"])
@requires_auth()
async def list_projects_by_lead(lead_id):
    user = request.user
    session = SessionLocal()
    try:
        lead = session.query(Lead).filter(
            Lead.id == lead_id,
            Lead.tenant_id == user.tenant_id,
            Lead.deleted_at == None,
        ).first()

        if not lead:
            return jsonify({"error": "Lead not found"}), 404

        if not any(role.name == "admin" for role in user.roles):
            if lead.assigned_to != user.id and lead.created_by != user.id:
                return jsonify({"error": "Forbidden"}), 403

        projects = session.query(Project).filter(
            Project.lead_id == lead_id,
            Project.tenant_id == user.tenant_id
        ).order_by(Project.created_at.desc()).all()

        return jsonify([
            {
                "id": p.id,
                "project_name": p.project_name,
                "type": p.type,
                "project_status": p.project_status,
                "project_description": p.project_description,
                "notes": p.notes,
                "project_start": p.project_start.isoformat() if p.project_start else None,
                "project_end": p.project_end.isoformat() if p.project_end else None,
                "project_worth": p.project_worth,
                "created_at": p.created_at.isoformat() if p.created_at else None,
                # NEW: Include contact fields
                "primary_contact_name": p.primary_contact_name,
                "primary_contact_title": p.primary_contact_title,
                "primary_contact_email": p.primary_contact_email,
                "primary_contact_phone": p.primary_contact_phone,
                "primary_contact_phone_label": p.primary_contact_phone_label
            } for p in projects
        ])
    finally:
        session.close()


@projects_bp.route("/<int:project_id>", methods=["DELETE"])
@requires_auth()
async def delete_project(project_id):
    user = request.user
    session = SessionLocal()
    try:
        project = session.query(Project).filter(
            Project.id == project_id,
            Project.tenant_id == user.tenant_id,
            Project.deleted_at == None  # Only allow deleting active items
        ).first()

        if not project:
            return jsonify({"error": "Project not found"}), 404

        project.deleted_at = datetime.utcnow()
        project.deleted_by = user.id

        session.commit()
        return jsonify({"message": "Project soft-deleted successfully"})
    finally:
        session.close()


@projects_bp.route("/trash", methods=["GET"])
@requires_auth()
async def list_trashed_projects():
    user = request.user
    session = SessionLocal()
    try:
        if not any(role.name == "admin" for role in user.roles):
            trashed = session.query(Project).filter(
                Project.tenant_id == user.tenant_id,
                Project.deleted_at != None,
                Project.created_by == user.id  # Only show user's own
            ).order_by(Project.deleted_at.desc()).all()
        else:
            trashed = session.query(Project).filter(
                Project.tenant_id == user.tenant_id,
                Project.deleted_at != None
            ).order_by(Project.deleted_at.desc()).all()

        return jsonify([
            {
                "id": p.id,
                "name": p.project_name,
                "deleted_at": p.deleted_at.isoformat() + "Z",
                "deleted_by": p.deleted_by
            } for p in trashed
        ])
    finally:
        session.close()


@projects_bp.route("/<int:project_id>/restore", methods=["PUT"])
@requires_auth()
async def restore_project(project_id):
    user = request.user
    session = SessionLocal()
    try:
        query = session.query(Project).filter(
            Project.id == project_id,
            Project.tenant_id == user.tenant_id,
            Project.deleted_at != None
        )

        # Non-admins can only restore their own deleted items
        if not any(role.name == "admin" for role in user.roles):
            query = query.filter(Project.created_by == user.id)

        project = query.first()
        if not project:
            return jsonify({"error": "Project not found or not authorized to restore"}), 404

        project.deleted_at = None
        project.deleted_by = None
        session.commit()
        return jsonify({"message": "Project restored successfully"}), 200
    finally:
        session.close()


@projects_bp.route("/<int:project_id>/purge", methods=["DELETE"])
@requires_auth(roles=["admin"])
async def purge_project(project_id):
    user = request.user
    session = SessionLocal()
    try:
        project = session.query(Project).filter(
            Project.id == project_id,
            Project.tenant_id == user.tenant_id,
            Project.deleted_at != None
        ).first()

        if not project:
            return jsonify({"error": "Project not found or not eligible for purge"}), 404

        session.delete(project)
        session.commit()
        return jsonify({"message": "Project permanently deleted"}), 200
    finally:
        session.close()


@projects_bp.route("/bulk-delete", methods=["POST"])
@requires_auth(roles=["admin"])
async def bulk_delete_projects():
    user = request.user
    data = await request.get_json()
    project_ids = data.get("project_ids", [])

    if not project_ids or not isinstance(project_ids, list):
        return jsonify({"error": "No project IDs provided"}), 400

    session = SessionLocal()
    try:
        updated_count = session.query(Project).filter(
            Project.tenant_id == user.tenant_id,
            Project.id.in_(project_ids),
            Project.deleted_at == None
        ).update(
            {Project.deleted_at: datetime.utcnow(), Project.deleted_by: user.id},
            synchronize_session=False
        )
        session.commit()
        return jsonify({"message": f"{updated_count} project(s) deleted"})
    finally:
        session.close()

