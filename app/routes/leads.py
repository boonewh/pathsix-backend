from quart import Blueprint, request, jsonify
from datetime import datetime
from pydantic import ValidationError
from app.models import Lead, ActivityLog, ActivityType, User
from app.database import SessionLocal
from app.utils.auth_utils import requires_auth
from app.utils.email_utils import send_assignment_notification
from app.utils.phone_utils import clean_phone_number
from app.constants import TYPE_OPTIONS, LEAD_STATUS_OPTIONS, PHONE_LABELS
from app.schemas.leads import LeadCreateSchema, LeadUpdateSchema, LeadAssignSchema
from sqlalchemy import or_, and_
from sqlalchemy.orm import joinedload

leads_bp = Blueprint("leads", __name__, url_prefix="/api/leads")


@leads_bp.route("", methods=["GET"])
@leads_bp.route("/", methods=["GET"])
@requires_auth()
async def list_leads():
    user = request.user
    session = SessionLocal()
    try:
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 20))
        sort_order = request.args.get("sort", "newest")
        
        # Validate sort order
        if sort_order not in ["newest", "oldest", "alphabetical"]:
            sort_order = "newest"

        query = session.query(Lead).options(
            joinedload(Lead.assigned_user),
            joinedload(Lead.created_by_user)
        ).filter(
            Lead.tenant_id == user.tenant_id,
            Lead.deleted_at == None,
            or_(
                Lead.assigned_to == user.id,
                and_(
                    Lead.assigned_to == None,
                    Lead.created_by == user.id
                )
            )
        )

        # Apply sorting
        if sort_order == "newest":
            query = query.order_by(Lead.created_at.desc())
        elif sort_order == "oldest":
            query = query.order_by(Lead.created_at.asc())
        elif sort_order == "alphabetical":
            query = query.order_by(Lead.name.asc())

        total = query.count()
        leads = query.offset((page - 1) * per_page).limit(per_page).all()

        response = jsonify({
            "leads": [{
                "id": l.id,
                "name": l.name,
                "contact_person": l.contact_person,
                "contact_title": l.contact_title,
                "email": l.email,
                "phone": l.phone,
                "phone_label": l.phone_label,
                "secondary_phone": l.secondary_phone,
                "secondary_phone_label": l.secondary_phone_label,
                "address": l.address,
                "city": l.city,
                "state": l.state,
                "zip": l.zip,
                "notes": l.notes,
                "created_at": l.created_at.isoformat() + "Z",
                "assigned_to": l.assigned_to,
                "assigned_to_name": (
                    l.assigned_user.email if l.assigned_user
                    else l.created_by_user.email if l.created_by_user
                    else None
                ),
                "lead_status": l.lead_status,
                "converted_on": l.converted_on.isoformat() + "Z" if l.converted_on else None,
                "type": l.type  
            } for l in leads],
            "total": total,
            "page": page,
            "per_page": per_page,
            "sort_order": sort_order
        })
        response.headers["Cache-Control"] = "no-store"
        return response
    finally:
        session.close()


@leads_bp.route("", methods=["POST"])
@leads_bp.route("/", methods=["POST"])
@requires_auth()
async def create_lead():
    print("DEBUG: create_lead function called!")
    user = request.user
    raw_data = await request.get_json()
    
    # Debug logging
    print(f"DEBUG: Raw data received: {raw_data}")
    print(f"DEBUG: About to validate with LeadCreateSchema")
    
    # Validate input using Pydantic schema
    try:
        data = LeadCreateSchema(**raw_data)
        print(f"DEBUG: Validation passed: {data.model_dump()}")
    except ValidationError as e:
        print(f"DEBUG: Validation failed: {e.errors()}")
        return jsonify({
            "error": "Validation failed",
            "details": e.errors()
        }), 400
    
    session = SessionLocal()
    try:
        lead = Lead(
            tenant_id=user.tenant_id,
            created_by=user.id,
            name=data.name,
            contact_person=data.contact_person,
            contact_title=data.contact_title,
            email=str(data.email) if data.email else None,
            phone=clean_phone_number(data.phone) if data.phone else None,
            phone_label=data.phone_label,
            secondary_phone=clean_phone_number(data.secondary_phone) if data.secondary_phone else None,
            secondary_phone_label=data.secondary_phone_label,
            address=data.address,
            city=data.city,
            state=data.state,
            zip=data.zip,
            notes=data.notes,
            type=data.type,
            lead_status=data.lead_status,
            created_at=datetime.utcnow()
        )
        
        session.add(lead)
        session.commit()
        session.refresh(lead)
        return jsonify({"id": lead.id}), 201
    finally:
        session.close()

@leads_bp.route("/<int:lead_id>", methods=["GET"])
@requires_auth()
async def get_lead(lead_id):
    user = request.user
    session = SessionLocal()
    try:
        lead_query = session.query(Lead).options(
            joinedload(Lead.assigned_user),
            joinedload(Lead.created_by_user)
        ).filter(
            Lead.id == lead_id,
            Lead.tenant_id == user.tenant_id,
            Lead.deleted_at == None
        )

        if not any(role.name == "admin" for role in user.roles):
            lead_query = lead_query.filter(
                or_(
                    Lead.created_by == user.id,
                    Lead.assigned_to == user.id
                )
            )

        lead = lead_query.first()

        if not lead:
            return jsonify({"error": "Lead not found"}), 404

        log = ActivityLog(
            tenant_id=user.tenant_id,
            user_id=user.id,
            action=ActivityType.viewed,
            entity_type="lead",
            entity_id=lead.id,
            description=f"Viewed lead '{lead.name}'"
        )
        session.add(log)
        session.commit()

        response = jsonify({
            "id": lead.id,
            "name": lead.name,
            "contact_person": lead.contact_person,
            "contact_title": lead.contact_title,
            "email": lead.email,
            "phone": lead.phone,
            "phone_label": lead.phone_label,
            "secondary_phone": lead.secondary_phone,
            "secondary_phone_label": lead.secondary_phone_label,
            "address": lead.address,
            "city": lead.city,
            "state": lead.state,
            "zip": lead.zip,
            "notes": lead.notes,
            "created_at": lead.created_at.isoformat() + "Z",
            "lead_status": lead.lead_status,
            "converted_on": lead.converted_on.isoformat() + "Z" if lead.converted_on else None,
            "type": lead.type,
            "contacts": [c.to_dict() for c in lead.contacts] if lead.contacts else []
        })
        response.headers["Cache-Control"] = "no-store"
        return response
    finally:
        session.close()


@leads_bp.route("/<int:lead_id>", methods=["PUT"])
@requires_auth()
async def update_lead(lead_id):
    user = request.user
    raw_data = await request.get_json()
    
    # Validate input using Pydantic schema
    try:
        data = LeadUpdateSchema(**raw_data)
    except ValidationError as e:
        return jsonify({
            "error": "Validation failed",
            "details": e.errors()
        }), 400
    
    session = SessionLocal()
    try:
        lead_query = session.query(Lead).filter(
            Lead.id == lead_id,
            Lead.tenant_id == user.tenant_id,
            Lead.deleted_at == None
        )

        if not any(role.name == "admin" for role in user.roles):
            lead_query = lead_query.filter(
                or_(
                    Lead.created_by == user.id,
                    Lead.assigned_to == user.id
                )
            )

        lead = lead_query.first()
        if not lead:
            return jsonify({"error": "Lead not found"}), 404

        # Update fields that were provided and validated
        update_data = data.dict(exclude_unset=True)
        
        for field, value in update_data.items():
            if field in ["phone", "secondary_phone"]:
                # Clean phone numbers
                cleaned_phone = clean_phone_number(value) if value else None
                setattr(lead, field, cleaned_phone)
            elif field == "email":
                # Convert EmailStr to string
                setattr(lead, field, str(value) if value else None)
            elif field == "lead_status":
                # Handle status change logic
                if value == "closed" and lead.lead_status != "closed":
                    lead.converted_on = datetime.utcnow()
                setattr(lead, field, value)
            else:
                setattr(lead, field, value)

        lead.updated_by = user.id
        lead.updated_at = datetime.utcnow()

        session.commit()
        session.refresh(lead)
        return jsonify({"id": lead.id})
    finally:
        session.close()


@leads_bp.route("/<int:lead_id>", methods=["DELETE"])
@requires_auth()
async def delete_lead(lead_id):
    user = request.user
    session = SessionLocal()
    try:
        lead_query = session.query(Lead).filter(
            Lead.id == lead_id,
            Lead.tenant_id == user.tenant_id
        )

        if not any(role.name == "admin" for role in user.roles):
            lead_query = lead_query.filter(
                or_(
                    Lead.created_by == user.id,
                    Lead.assigned_to == user.id
                )
            )

        lead = lead_query.first()
        if not lead:
            return jsonify({"error": "Lead not found"}), 404

        if lead.deleted_at is not None:
            return jsonify({"message": "Lead already deleted"}), 200

        lead.deleted_at = datetime.utcnow()
        lead.deleted_by = user.id
        session.commit()
        return jsonify({"message": "Lead soft-deleted successfully"})
    finally:
        session.close()



@leads_bp.route("/<int:lead_id>/assign", methods=["PUT"])
@requires_auth(roles=["admin"])
async def assign_lead(lead_id):
    user = request.user
    raw_data = await request.get_json()
    
    # Validate input using Pydantic schema
    try:
        data = LeadAssignSchema(**raw_data)
    except ValidationError as e:
        return jsonify({
            "error": "Validation failed",
            "details": e.errors()
        }), 400

    session = SessionLocal()
    try:
        lead = session.query(Lead).filter(
            Lead.id == lead_id,
            Lead.tenant_id == user.tenant_id,
            Lead.deleted_at == None
        ).first()

        if not lead:
            return jsonify({"error": "Lead not found"}), 404

        # Validate that assigned_to is a valid user
        assigned_user = session.query(User).filter(
            User.id == data.assigned_to,
            User.tenant_id == user.tenant_id,
            User.is_active == True
        ).first()
        
        if not assigned_user:
            return jsonify({"error": f"User {data.assigned_to} not found or not active"}), 400

        lead.assigned_to = data.assigned_to
        lead.updated_by = user.id
        lead.updated_at = datetime.utcnow()

        # Send email to assigned user (before commit in case it fails)
        assigned_user = session.query(User).get(data.assigned_to)
        if assigned_user:
            try:
                await send_assignment_notification(
                    to_email=assigned_user.email,
                    entity_type="lead",
                    entity_name=lead.name,
                    assigned_by=user.email
                )
            except Exception as email_error:
                print(f"DEBUG: Email notification failed: {email_error}")
                # Don't fail the assignment if email fails

        try:
            session.commit()
            return jsonify({"message": "Lead assigned successfully"})
        except Exception as e:
            session.rollback()
            return jsonify({"error": f"Database error: {str(e)}"}), 500

    except Exception as e:
        return jsonify({"error": f"Unexpected error: {str(e)}"}), 500
    finally:
        session.close()


@leads_bp.route("/all", methods=["GET"])
@requires_auth(roles=["admin"])
async def list_all_leads_admin():
    user = request.user
    session = SessionLocal()
    try:
        # Get pagination parameters
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 20))
        sort_order = request.args.get("sort", "newest")
        user_email = request.args.get("user_email")  # Filter by specific user
        
        # Validate sort order
        if sort_order not in ["newest", "oldest", "alphabetical"]:
            sort_order = "newest"

        query = session.query(Lead).options(
            joinedload(Lead.assigned_user),
            joinedload(Lead.created_by_user)
        ).filter(
            Lead.tenant_id == user.tenant_id,
            Lead.deleted_at == None
        )

        # Filter by user if specified
        if user_email:
            query = query.filter(
                or_(
                    Lead.assigned_user.has(User.email == user_email),
                    Lead.created_by_user.has(User.email == user_email)
                )
            )

        # Apply sorting
        if sort_order == "newest":
            query = query.order_by(Lead.created_at.desc())
        elif sort_order == "oldest":
            query = query.order_by(Lead.created_at.asc())
        elif sort_order == "alphabetical":
            query = query.order_by(Lead.name.asc())

        total = query.count()
        leads = query.offset((page - 1) * per_page).limit(per_page).all()

        response_data = {
            "leads": [{
                "id": l.id,
                "name": l.name,
                "contact_person": l.contact_person,
                "contact_title": l.contact_title,
                "email": l.email,
                "phone": l.phone,
                "phone_label": l.phone_label,
                "secondary_phone": l.secondary_phone,
                "secondary_phone_label": l.secondary_phone_label,
                "address": l.address,
                "city": l.city,
                "state": l.state,
                "zip": l.zip,
                "notes": l.notes,
                "assigned_to": l.assigned_to,
                "created_at": l.created_at.isoformat() + "Z",
                "lead_status": l.lead_status,
                "converted_on": l.converted_on.isoformat() + "Z" if l.converted_on else None,
                "type": l.type,
                "assigned_to_name": (
                    l.assigned_user.email if l.assigned_user
                    else l.created_by_user.email if l.created_by_user
                    else None
                ),
                "created_by_name": l.created_by_user.email if l.created_by_user else None,
            } for l in leads],
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


@leads_bp.route("/assigned", methods=["GET"])
@requires_auth(roles=["admin"])
async def list_assigned_leads():
    user = request.user
    session = SessionLocal()
    try:
        leads = session.query(Lead).filter(
            Lead.tenant_id == user.tenant_id,
            Lead.deleted_at == None,
            Lead.assigned_to != None
        ).all()

        response = jsonify([{
            "id": l.id,
            "name": l.name,
            "contact_person": l.contact_person,
            "contact_title": l.contact_title,
            "email": l.email,
            "phone": l.phone,
            "phone_label": l.phone_label,
            "secondary_phone": l.secondary_phone,
            "secondary_phone_label": l.secondary_phone_label,
            "address": l.address,
            "city": l.city,
            "state": l.state,
            "zip": l.zip,
            "notes": l.notes,
            "assigned_to": l.assigned_to,
            "created_at": l.created_at.isoformat() + "Z",
            "lead_status": l.lead_status,
            "converted_on": l.converted_on.isoformat() + "Z" if l.converted_on else None,
            "type": l.type
        } for l in leads])
        response.headers["Cache-Control"] = "no-store"
        return response
    finally:
        session.close()


@leads_bp.route("/bulk-delete", methods=["POST"])
@requires_auth(roles=["admin"])
async def bulk_delete_leads():
    user = request.user
    data = await request.get_json()
    lead_ids = data.get("lead_ids", [])

    if not lead_ids or not isinstance(lead_ids, list):
        return jsonify({"error": "No lead IDs provided"}), 400

    session = SessionLocal()
    try:
        # Soft delete only leads that belong to this tenant and haven't already been deleted
        updated_count = session.query(Lead).filter(
            Lead.tenant_id == user.tenant_id,
            Lead.id.in_(lead_ids),
            Lead.deleted_at == None
        ).update(
            {Lead.deleted_at: datetime.utcnow(), Lead.deleted_by: user.id},
            synchronize_session=False
        )
        session.commit()
        return jsonify({"message": f"{updated_count} lead(s) deleted"})
    finally:
        session.close()

@leads_bp.route("/bulk-purge", methods=["DELETE", "POST"])
@requires_auth(roles=["admin"])
async def bulk_purge_leads():
    user = request.user
    data = await request.get_json()
    lead_ids = data.get("lead_ids", [])

    if not lead_ids or not isinstance(lead_ids, list):
        return jsonify({"error": "No lead IDs provided"}), 400

    session = SessionLocal()
    try:
        # Only purge leads that are already soft-deleted
        deleted_count = session.query(Lead).filter(
            Lead.tenant_id == user.tenant_id,
            Lead.id.in_(lead_ids),
            Lead.deleted_at != None
        ).delete(synchronize_session=False)
        
        session.commit()
        return jsonify({"message": f"{deleted_count} lead(s) permanently deleted"})
    finally:
        session.close()

@leads_bp.route("/trash", methods=["GET"])
@requires_auth()
async def list_trashed_leads():
    user = request.user
    session = SessionLocal()
    try:
        if not any(role.name == "admin" for role in user.roles):
            trashed = session.query(Lead).filter(
                Lead.tenant_id == user.tenant_id,
                Lead.deleted_at != None,
                or_(
                    Lead.created_by == user.id,
                    Lead.assigned_to == user.id
                )
            ).order_by(Lead.deleted_at.desc()).all()
        else:
            trashed = session.query(Lead).filter(
                Lead.tenant_id == user.tenant_id,
                Lead.deleted_at != None
            ).order_by(Lead.deleted_at.desc()).all()

        return jsonify([
            {
                "id": l.id,
                "name": l.name,
                "deleted_at": l.deleted_at.isoformat() + "Z",
                "deleted_by": l.deleted_by
            } for l in trashed
        ])
    finally:
        session.close()


@leads_bp.route("/<int:lead_id>/restore", methods=["PUT"])
@requires_auth()
async def restore_lead(lead_id):
    user = request.user
    session = SessionLocal()
    try:
        lead = session.query(Lead).filter(
            Lead.id == lead_id,
            Lead.tenant_id == user.tenant_id,
            Lead.deleted_at != None,
            or_(
                Lead.created_by == user.id,
                Lead.assigned_to == user.id,
                # Optional: allow admin to restore any
                User.id == user.id if any(role.name == "admin" for role in user.roles) else False
            )
        ).first()

        if not lead:
            return jsonify({"error": "Lead not found or not authorized to restore"}), 404

        lead.deleted_at = None
        lead.deleted_by = None
        session.commit()
        return jsonify({"message": "Lead restored successfully"})
    finally:
        session.close()


@leads_bp.route("/<int:lead_id>/purge", methods=["DELETE"])
@requires_auth(roles=["admin"])
async def purge_lead(lead_id):
    user = request.user
    session = SessionLocal()
    try:
        lead = session.query(Lead).filter(
            Lead.id == lead_id,
            Lead.tenant_id == user.tenant_id,
            Lead.deleted_at != None
        ).first()

        if not lead:
            return jsonify({"error": "Lead not found or not eligible for purge"}), 404

        session.delete(lead)
        session.commit()
        return jsonify({"message": "Lead permanently deleted"}), 200
    finally:
        session.close()

