from quart import Blueprint, request, jsonify, Response
from datetime import datetime
from pydantic import ValidationError
from sqlalchemy.orm import joinedload
from sqlalchemy import or_, and_, func
from icalendar import Calendar, Event

from app.models import Interaction, Client, Lead, Project, FollowUpStatus, User, ActivityLog, ActivityType
from app.database import SessionLocal
from app.utils.auth_utils import requires_auth
from app.schemas.interactions import InteractionCreateSchema, InteractionUpdateSchema

interactions_bp = Blueprint("interactions", __name__, url_prefix="/api/interactions")


@interactions_bp.route("", methods=["GET"])
@interactions_bp.route("/", methods=["GET"])
@requires_auth()
async def list_interactions():
    user = request.user
    session = SessionLocal()
    try:
        client_id = request.args.get("client_id")
        lead_id = request.args.get("lead_id")
        project_id = request.args.get("project_id")  # NEW: Add project support
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 10))
        sort_order = request.args.get("sort", "newest")

        # Validate only one entity type is specified
        entity_count = sum(bool(x) for x in [client_id, lead_id, project_id])
        if entity_count > 1:
            return jsonify({"error": "Cannot filter by multiple entity types"}), 400

        # Validate sort order
        valid_sorts = ["newest", "oldest", "pending", "completed"]
        if sort_order not in valid_sorts:
            sort_order = "newest"

        query = session.query(Interaction).options(
            joinedload(Interaction.client),
            joinedload(Interaction.lead),
            joinedload(Interaction.project)  # NEW: Add project loading
        ).filter(Interaction.tenant_id == user.tenant_id)

        # Apply entity-based access control
        if not any(role.name == "admin" for role in user.roles):
            query = query.filter(
                or_(
                    # Client interactions - user has access to client
                    and_(
                        Interaction.client_id != None,
                        Interaction.client.has(
                            or_(
                                Client.created_by == user.id,
                                Client.assigned_to == user.id
                            )
                        )
                    ),
                    # Lead interactions - user has access to lead
                    and_(
                        Interaction.lead_id != None,
                        Interaction.lead.has(
                            or_(
                                Lead.created_by == user.id,
                                Lead.assigned_to == user.id
                            )
                        )
                    ),
                    # Project interactions - user created the project
                    and_(
                        Interaction.project_id != None,
                        Interaction.project.has(Project.created_by == user.id)
                    )
                )
            )

        # Apply entity-specific filters
        if client_id:
            query = query.filter(
                Interaction.client_id == int(client_id),
                Interaction.lead_id == None,
                Interaction.project_id == None
            )
        elif lead_id:
            query = query.filter(
                Interaction.lead_id == int(lead_id),
                Interaction.client_id == None,
                Interaction.project_id == None
            )
        elif project_id:  # NEW: Project filtering
            query = query.filter(
                Interaction.project_id == int(project_id),
                Interaction.client_id == None,
                Interaction.lead_id == None
            )

        # Apply sorting
        if sort_order == "newest":
            query = query.order_by(Interaction.contact_date.desc())
        elif sort_order == "oldest":
            query = query.order_by(Interaction.contact_date.asc())
        elif sort_order == "pending":
            query = query.order_by(
                (and_(
                    Interaction.follow_up != None,
                    Interaction.followup_status != FollowUpStatus.completed
                )).desc(),
                Interaction.follow_up.asc(),
                Interaction.contact_date.desc()
            )
        elif sort_order == "completed":
            query = query.order_by(
                (Interaction.followup_status == FollowUpStatus.completed).desc(),
                Interaction.contact_date.desc()
            )

        total = query.count()
        interactions = query.offset((page - 1) * per_page).limit(per_page).all()

        response_data = {
            "interactions": [
                {
                    "id": i.id,
                    "contact_date": i.contact_date.isoformat(),
                    "follow_up": i.follow_up.isoformat() if i.follow_up else None,
                    "summary": i.summary,
                    "outcome": i.outcome,
                    "notes": i.notes,
                    "client_id": i.client_id,
                    "lead_id": i.lead_id,
                    "project_id": i.project_id,  # NEW: Include project_id
                    "client_name": i.client.name if i.client else None,
                    "lead_name": i.lead.name if i.lead else None,
                    "project_name": i.project.project_name if i.project else None,  # NEW: Project name
                    "contact_person": (
                        i.contact_person or  # Use interaction's contact if set
                        (i.client.contact_person if i.client else None) or
                        (i.lead.contact_person if i.lead else None) or
                        (i.project.primary_contact_name if i.project else None)  # NEW: Project contact
                    ),
                    "email": (
                        i.email or  # Use interaction's email if set
                        (i.client.email if i.client else None) or
                        (i.lead.email if i.lead else None) or
                        (i.project.primary_contact_email if i.project else None)  # NEW: Project email
                    ),
                    "phone": (
                        i.phone or  # Use interaction's phone if set
                        (i.client.phone if i.client else None) or
                        (i.lead.phone if i.lead else None) or
                        (i.project.primary_contact_phone if i.project else None)  # NEW: Project phone
                    ),
                    "phone_label": (
                        (i.client.phone_label if i.client else None) or
                        (i.lead.phone_label if i.lead else None) or
                        (i.project.primary_contact_phone_label if i.project else None) or
                        "work"
                    ),
                    "secondary_phone": (
                        (i.client.secondary_phone if i.client else None) or
                        (i.lead.secondary_phone if i.lead else None)
                        # NOTE: Projects only have primary contact for now
                    ),
                    "secondary_phone_label": (
                        (i.client.secondary_phone_label if i.client else None) or
                        (i.lead.secondary_phone_label if i.lead else None)
                    ),
                    "followup_status": i.followup_status.value if i.followup_status else None,
                    "profile_link": (
                        f"/clients/{i.client_id}" if i.client_id else
                        f"/leads/{i.lead_id}" if i.lead_id else
                        f"/projects/{i.project_id}" if i.project_id else None  # NEW: Project link
                    )
                } for i in interactions
            ],
            "total": total,
            "page": page,
            "per_page": per_page,
            "sort_order": sort_order
        }

        response = jsonify(response_data)
        response.headers["Cache-Control"] = "no-store"
        return response
    finally:
        session.close()


@interactions_bp.route("", methods=["POST"])
@interactions_bp.route("/", methods=["POST"])
@requires_auth()
async def create_interaction():
    user = request.user
    raw_data = await request.get_json()
    
    # Validate input using Pydantic schema
    try:
        data = InteractionCreateSchema(**raw_data)
    except ValidationError as e:
        return jsonify({
            "error": "Validation failed",
            "details": e.errors()
        }), 400

    session = SessionLocal()
    try:
        # Validate exactly one entity is specified
        entity_ids = [data.client_id, data.lead_id, data.project_id]
        entity_count = sum(bool(x) for x in entity_ids)
        
        if entity_count != 1:
            return jsonify({"error": "Interaction must link to exactly one entity (client, lead, or project)"}), 400

        # Validate user has access to the entity
        if data.client_id:
            entity = session.query(Client).filter(
                Client.id == data.client_id,
                Client.tenant_id == user.tenant_id,
                Client.deleted_at == None
            ).first()
            if not entity:
                return jsonify({"error": "Client not found"}), 404
            if not any(role.name == "admin" for role in user.roles):
                if entity.created_by != user.id and entity.assigned_to != user.id:
                    return jsonify({"error": "Access denied to this client"}), 403
                    
        elif data.lead_id:
            entity = session.query(Lead).filter(
                Lead.id == data.lead_id,
                Lead.tenant_id == user.tenant_id,
                Lead.deleted_at == None
            ).first()
            if not entity:
                return jsonify({"error": "Lead not found"}), 404
            if not any(role.name == "admin" for role in user.roles):
                if entity.created_by != user.id and entity.assigned_to != user.id:
                    return jsonify({"error": "Access denied to this lead"}), 403
                    
        elif data.project_id:
            entity = session.query(Project).filter(
                Project.id == data.project_id,
                Project.tenant_id == user.tenant_id
            ).first()
            if not entity:
                return jsonify({"error": "Project not found"}), 404
            if not any(role.name == "admin" for role in user.roles):
                if entity.created_by != user.id:
                    return jsonify({"error": "Access denied to this project"}), 403

        interaction = Interaction(
            tenant_id=user.tenant_id,
            client_id=data.client_id,
            lead_id=data.lead_id,
            project_id=data.project_id,
            contact_date=data.contact_date,
            summary=data.summary,
            outcome=data.outcome,
            notes=data.notes,
            follow_up=data.follow_up,
            contact_person=data.contact_person,
            email=str(data.email) if data.email else None,
            phone=data.phone
        )
        session.add(interaction)
        session.commit()
        session.refresh(interaction)

        return jsonify({"id": interaction.id}), 201
    finally:
        session.close()


@interactions_bp.route("/<int:interaction_id>", methods=["PUT"])
@requires_auth()
async def update_interaction(interaction_id):
    user = request.user
    raw_data = await request.get_json()
    
    # Validate input using Pydantic schema
    try:
        data = InteractionUpdateSchema(**raw_data)
    except ValidationError as e:
        return jsonify({
            "error": "Validation failed",
            "details": e.errors()
        }), 400

    session = SessionLocal()
    try:
        interaction = session.query(Interaction).options(
            joinedload(Interaction.client),
            joinedload(Interaction.lead),
            joinedload(Interaction.project)
        ).filter(
            Interaction.id == interaction_id,
            Interaction.tenant_id == user.tenant_id
        ).first()

        if not interaction:
            return jsonify({"error": "Interaction not found"}), 404

        # Validate user has access to the associated entity
        if not any(role.name == "admin" for role in user.roles):
            has_access = False
            if interaction.client_id:
                has_access = interaction.client.created_by == user.id or interaction.client.assigned_to == user.id
            elif interaction.lead_id:
                has_access = interaction.lead.created_by == user.id or interaction.lead.assigned_to == user.id
            elif interaction.project_id:
                has_access = interaction.project.created_by == user.id
                
            if not has_access:
                return jsonify({"error": "Access denied"}), 403

        # Update fields with validated data
        update_data = data.model_dump(exclude_unset=True)
        
        for field, value in update_data.items():
            if field == "email":
                interaction.email = str(value) if value else None
            else:
                setattr(interaction, field, value)

        session.commit()
        session.refresh(interaction)
        return jsonify({"id": interaction.id})
    finally:
        session.close()


@interactions_bp.route("/<int:interaction_id>", methods=["DELETE"])
@requires_auth()
async def delete_interaction(interaction_id):
    user = request.user
    session = SessionLocal()
    try:
        interaction = session.query(Interaction).options(
            joinedload(Interaction.client),
            joinedload(Interaction.lead),
            joinedload(Interaction.project)  # NEW: Load project
        ).filter(
            Interaction.id == interaction_id,
            Interaction.tenant_id == user.tenant_id
        ).first()

        if not interaction:
            return jsonify({"error": "Interaction not found"}), 404

        # Validate user has access to delete
        if not any(role.name == "admin" for role in user.roles):
            has_access = False
            if interaction.client_id:
                has_access = interaction.client.created_by == user.id or interaction.client.assigned_to == user.id
            elif interaction.lead_id:
                has_access = interaction.lead.created_by == user.id or interaction.lead.assigned_to == user.id
            elif interaction.project_id:  # NEW: Project access check
                has_access = interaction.project.created_by == user.id
                
            if not has_access:
                return jsonify({"error": "Access denied"}), 403

        session.delete(interaction)
        session.commit()
        return jsonify({"message": "Interaction deleted"})
    finally:
        session.close()


@interactions_bp.route("/transfer", methods=["POST"])
@requires_auth()
async def transfer_interactions():
    data = await request.get_json()
    from_lead_id = data.get("from_lead_id")
    to_client_id = data.get("to_client_id")
    user = request.user

    if not from_lead_id or not to_client_id:
        return jsonify({"error": "Missing from_lead_id or to_client_id"}), 400

    session = SessionLocal()
    try:
        interactions = session.query(Interaction).filter(
            Interaction.tenant_id == user.tenant_id,
            Interaction.lead_id == from_lead_id
        ).all()

        for interaction in interactions:
            interaction.lead_id = None
            interaction.client_id = to_client_id

        session.commit()

        return jsonify({
            "success": True,
            "transferred": len(interactions)
        })
    finally:
        session.close()


@interactions_bp.route("/<int:interaction_id>/calendar.ics", methods=["GET"])
async def get_interaction_ics(interaction_id):
    session = SessionLocal()
    try:
        interaction = session.query(Interaction).options(
            joinedload(Interaction.client),
            joinedload(Interaction.lead),
            joinedload(Interaction.project)  # NEW: Load project
        ).filter(
            Interaction.id == interaction_id
        ).first()

        if not interaction:
            return Response("Interaction not found", status=404)

        if not interaction.follow_up:
            return Response("This interaction has no follow-up date", status=400)

        cal = Calendar()
        cal.add("prodid", "-//PathSix CRM//EN")
        cal.add("version", "2.0")

        # Determine entity name for calendar event
        entity_name = (
            interaction.client.name if interaction.client else
            interaction.lead.name if interaction.lead else
            interaction.project.project_name if interaction.project else  # NEW: Project name
            "CRM Entity"
        )

        contact_name = (
            interaction.contact_person or
            (interaction.client.contact_person if interaction.client else None) or
            (interaction.lead.contact_person if interaction.lead else None) or
            (interaction.project.primary_contact_name if interaction.project else None) or  # NEW: Project contact
            "Contact"
        )

        event = Event()
        event.add("summary", f"Follow-up: {entity_name} - {contact_name}")
        event.add("dtstart", interaction.follow_up)
        event.add("dtend", interaction.follow_up)
        event.add("dtstamp", interaction.contact_date)
        event.add("description", f"Outcome: {interaction.outcome or ''}\nNotes: {interaction.notes or ''}")
        
        # Build location string with contact info
        location_parts = []
        if interaction.phone or (interaction.client and interaction.client.phone) or (interaction.lead and interaction.lead.phone) or (interaction.project and interaction.project.primary_contact_phone):
            phone = (interaction.phone or 
                    (interaction.client.phone if interaction.client else None) or
                    (interaction.lead.phone if interaction.lead else None) or
                    (interaction.project.primary_contact_phone if interaction.project else None))
            location_parts.append(f"Phone: {phone}")
            
        if interaction.email or (interaction.client and interaction.client.email) or (interaction.lead and interaction.lead.email) or (interaction.project and interaction.project.primary_contact_email):
            email = (interaction.email or 
                    (interaction.client.email if interaction.client else None) or
                    (interaction.lead.email if interaction.lead else None) or
                    (interaction.project.primary_contact_email if interaction.project else None))
            location_parts.append(f"Email: {email}")
            
        event.add("location", "\n".join(location_parts))
        event["uid"] = f"interaction-{interaction.id}@pathsixcrm"

        cal.add_component(event)
        ics_content = cal.to_ical()

        return Response(
            ics_content,
            content_type="text/calendar",
            headers={
                "Content-Disposition": f"attachment; filename=interaction-{interaction.id}.ics"
            }
        )
    finally:
        session.close()


@interactions_bp.route("/<int:interaction_id>/complete", methods=["PUT"])
@requires_auth()
async def complete_interaction(interaction_id):
    user = request.user
    session = SessionLocal()
    try:
        interaction = session.query(Interaction).options(
            joinedload(Interaction.client),
            joinedload(Interaction.lead),
            joinedload(Interaction.project)  # NEW: Load project
        ).filter(
            Interaction.id == interaction_id,
            Interaction.tenant_id == user.tenant_id
        ).first()

        if not interaction:
            return jsonify({"error": "Interaction not found"}), 404

        # Validate user has access
        if not any(role.name == "admin" for role in user.roles):
            has_access = False
            if interaction.client_id:
                has_access = interaction.client.created_by == user.id or interaction.client.assigned_to == user.id
            elif interaction.lead_id:
                has_access = interaction.lead.created_by == user.id or interaction.lead.assigned_to == user.id
            elif interaction.project_id:  # NEW: Project access check
                has_access = interaction.project.created_by == user.id
                
            if not has_access:
                return jsonify({"error": "Access denied"}), 403

        interaction.followup_status = FollowUpStatus.completed
        session.commit()
        return jsonify({"message": "Interaction marked as completed"})
    finally:
        session.close()


@interactions_bp.route("/all", methods=["GET"])
@requires_auth(roles=["admin"])
async def list_all_interactions_admin():
    user = request.user
    session = SessionLocal()
    try:
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 20))
        sort_order = request.args.get("sort", "newest")
        user_email = request.args.get("user_email")
        
        if sort_order not in ["newest", "oldest", "alphabetical"]:
            sort_order = "newest"

        query = session.query(Interaction).options(
            joinedload(Interaction.client).joinedload(Client.assigned_user),
            joinedload(Interaction.client).joinedload(Client.created_by_user),
            joinedload(Interaction.lead).joinedload(Lead.assigned_user),
            joinedload(Interaction.lead).joinedload(Lead.created_by_user),
            joinedload(Interaction.project)  # NEW: Add project loading
        ).filter(
            Interaction.tenant_id == user.tenant_id
        )

        # Filter by user if specified
        if user_email:
            query = query.filter(
                or_(
                    # Client interactions
                    and_(
                        Interaction.client_id != None,
                        or_(
                            Interaction.client.has(Client.assigned_user.has(User.email == user_email)),
                            Interaction.client.has(Client.created_by_user.has(User.email == user_email))
                        )
                    ),
                    # Lead interactions
                    and_(
                        Interaction.lead_id != None,
                        or_(
                            Interaction.lead.has(Lead.assigned_user.has(User.email == user_email)),
                            Interaction.lead.has(Lead.created_by_user.has(User.email == user_email))
                        )
                    ),
                    # Project interactions - NEW: Add project filtering
                    and_(
                        Interaction.project_id != None,
                        Interaction.project.has(Project.created_by == 
                            session.query(User.id).filter(User.email == user_email).scalar_subquery()
                        )
                    )
                )
            )

        # Apply sorting
        if sort_order == "newest":
            query = query.order_by(Interaction.contact_date.desc())
        elif sort_order == "oldest":
            query = query.order_by(Interaction.contact_date.asc())
        elif sort_order == "alphabetical":
            # Sort by entity name alphabetically
            query = query.order_by(
                func.coalesce(Client.name, Lead.name, Project.project_name).asc()  # NEW: Include project name
            ).outerjoin(Client, Interaction.client_id == Client.id)\
             .outerjoin(Lead, Interaction.lead_id == Lead.id)\
             .outerjoin(Project, Interaction.project_id == Project.id)  # NEW: Join projects

        total = query.count()
        interactions = query.offset((page - 1) * per_page).limit(per_page).all()

        response_data = {
            "interactions": [{
                "id": i.id,
                "contact_date": i.contact_date.isoformat(),
                "follow_up": i.follow_up.isoformat() if i.follow_up else None,
                "summary": i.summary,
                "outcome": i.outcome,
                "notes": i.notes,
                "client_id": i.client_id,
                "lead_id": i.lead_id,
                "project_id": i.project_id,  # NEW: Include project_id
                "client_name": i.client.name if i.client else None,
                "lead_name": i.lead.name if i.lead else None,
                "project_name": i.project.project_name if i.project else None,  # NEW: Project name
                "contact_person": (
                    i.contact_person.strip() if i.contact_person and i.contact_person.strip()
                    else i.client.contact_person if i.client
                    else i.lead.contact_person if i.lead
                    else i.project.primary_contact_name if i.project  # NEW: Project contact
                    else None
                ),
                "email": (
                    i.email or
                    (i.client.email if i.client else None) or
                    (i.lead.email if i.lead else None) or
                    (i.project.primary_contact_email if i.project else None)  # NEW: Project email
                ),
                "phone": (
                    i.phone or
                    (i.client.phone if i.client else None) or
                    (i.lead.phone if i.lead else None) or
                    (i.project.primary_contact_phone if i.project else None)  # NEW: Project phone
                ),
                "followup_status": i.followup_status.value if i.followup_status else None,
                "profile_link": (
                    f"/clients/{i.client_id}" if i.client_id else
                    f"/leads/{i.lead_id}" if i.lead_id else
                    f"/projects/{i.project_id}" if i.project_id else None  # NEW: Project link
                ),
                "assigned_to_name": (
                    i.client.assigned_user.email if i.client and i.client.assigned_user
                    else i.client.created_by_user.email if i.client and i.client.created_by_user
                    else i.lead.assigned_user.email if i.lead and i.lead.assigned_user
                    else i.lead.created_by_user.email if i.lead and i.lead.created_by_user
                    else None  # NOTE: Projects don't have assigned_to yet, only created_by
                )
            } for i in interactions],
            "total": total,
            "page": page,
            "per_page": per_page,
            "sort_order": sort_order,
            "user_email": user_email
        }

        response = jsonify(response_data)
        response.headers["Cache-Control"] = "no-store"
        return response
    finally:
        session.close()