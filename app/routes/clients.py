from quart import Blueprint, request, jsonify
from datetime import datetime, timedelta
from app.models import Client, ActivityLog, ActivityType, User, Interaction
from app.database import SessionLocal
from app.utils.auth_utils import requires_auth
from app.utils.email_utils import send_assignment_notification
from app.utils.phone_utils import clean_phone_number
from app.constants import TYPE_OPTIONS, PHONE_LABELS
from sqlalchemy import or_, and_, func, desc
from sqlalchemy.orm import joinedload

clients_bp = Blueprint("clients", __name__, url_prefix="/api/clients")

@clients_bp.route("/", methods=["GET"])
@requires_auth()
async def list_clients():
    user = request.user
    session = SessionLocal()
    try:
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 20))
        sort_order = request.args.get("sort", "newest")
        activity_filter = request.args.get("activity_filter", "all")  # NEW: Activity filter
        
        # Validate sort order
        if sort_order not in ["newest", "oldest", "alphabetical", "activity"]:
            sort_order = "newest"

        # Base query with interaction data
        query = session.query(Client).options(
            joinedload(Client.assigned_user),
            joinedload(Client.created_by_user)
        ).filter(
            Client.tenant_id == user.tenant_id,
            Client.deleted_at == None,
            or_(
                Client.assigned_to == user.id,
                and_(
                    Client.assigned_to == None,
                    Client.created_by == user.id
                )
            )
        )

        # Apply activity filtering
        if activity_filter == "active":
            # Clients with interactions in last 30 days
            thirty_days_ago = datetime.utcnow() - timedelta(days=30)
            query = query.join(Interaction, Client.id == Interaction.client_id).filter(
                Interaction.contact_date >= thirty_days_ago
            ).distinct()
        elif activity_filter == "inactive":
            # Clients with no interactions in last 90 days OR no interactions at all
            ninety_days_ago = datetime.utcnow() - timedelta(days=90)
            
            # Subquery for clients with recent interactions
            recent_interaction_clients = session.query(Interaction.client_id).filter(
                Interaction.client_id != None,
                Interaction.contact_date >= ninety_days_ago
            ).distinct().subquery()
            
            # Exclude clients with recent interactions
            query = query.outerjoin(
                recent_interaction_clients, 
                Client.id == recent_interaction_clients.c.client_id
            ).filter(recent_interaction_clients.c.client_id == None)
        elif activity_filter == "new":
            # Clients created in last 7 days
            seven_days_ago = datetime.utcnow() - timedelta(days=7)
            query = query.filter(Client.created_at >= seven_days_ago)

        # Apply sorting
        if sort_order == "newest":
            query = query.order_by(Client.created_at.desc())
        elif sort_order == "oldest":
            query = query.order_by(Client.created_at.asc())
        elif sort_order == "alphabetical":
            query = query.order_by(Client.name.asc())
        elif sort_order == "activity":
            # Sort by most recent interaction date
            query = query.outerjoin(Interaction, Client.id == Interaction.client_id)\
                         .group_by(Client.id)\
                         .order_by(desc(func.max(Interaction.contact_date)))

        total = query.count()
        clients = query.offset((page - 1) * per_page).limit(per_page).all()

        # Get interaction statistics for each client
        client_ids = [c.id for c in clients]
        interaction_stats = {}
        
        if client_ids:
            # Get interaction counts and last interaction dates
            interaction_data = session.query(
                Interaction.client_id,
                func.count(Interaction.id).label('interaction_count'),
                func.max(Interaction.contact_date).label('last_interaction_date')
            ).filter(
                Interaction.client_id.in_(client_ids)
            ).group_by(Interaction.client_id).all()
            
            for data in interaction_data:
                interaction_stats[data.client_id] = {
                    'interaction_count': data.interaction_count,
                    'last_interaction_date': data.last_interaction_date.isoformat() + "Z" if data.last_interaction_date else None
                }

        response = jsonify({
            "clients": [{
                "id": c.id,
                "name": c.name,
                "contact_person": c.contact_person,
                "contact_title": c.contact_title,
                "email": c.email,
                "phone": c.phone,
                "phone_label": c.phone_label,
                "secondary_phone": c.secondary_phone,
                "secondary_phone_label": c.secondary_phone_label,
                "address": c.address,
                "city": c.city,
                "state": c.state,
                "zip": c.zip,
                "notes": c.notes,
                "type": c.type,
                "created_at": c.created_at.isoformat() + "Z",
                "assigned_to": c.assigned_to,
                "assigned_to_name": (
                    c.assigned_user.email if c.assigned_user
                    else c.created_by_user.email if c.created_by_user
                    else None
                ),
                # NEW: Interaction statistics
                "interaction_count": interaction_stats.get(c.id, {}).get('interaction_count', 0),
                "last_interaction_date": interaction_stats.get(c.id, {}).get('last_interaction_date'),
            } for c in clients],
            "total": total,
            "page": page,
            "per_page": per_page,
            "sort_order": sort_order,
            "activity_filter": activity_filter  # NEW: Include filter in response
        })
        response.headers["Cache-Control"] = "no-store"
        return response
    finally:
        session.close()


@clients_bp.route("/", methods=["POST"])
@requires_auth()
async def create_client():
    user = request.user
    data = await request.get_json()
    session = SessionLocal()
    try:
        client_type = data.get("type", TYPE_OPTIONS[0])
        if client_type not in TYPE_OPTIONS:
            client_type = TYPE_OPTIONS[0]

        client = Client(
            tenant_id=user.tenant_id,
            created_by=user.id,
            name=data["name"],
            contact_person=data.get("contact_person"),
            contact_title=data.get("contact_title"),
            email=data.get("email"),
            phone=clean_phone_number(data.get("phone")) if data.get("phone") else None,
            phone_label=data.get("phone_label", PHONE_LABELS[0]),
            secondary_phone=clean_phone_number(data.get("secondary_phone")) if data.get("secondary_phone") else None,
            secondary_phone_label=data.get("secondary_phone_label"),
            address=data.get("address"),
            city=data.get("city"),
            state=data.get("state"),
            zip=data.get("zip"),
            notes=data.get("notes"),
            type=client_type,
            created_at=datetime.utcnow()
        )
        session.add(client)
        session.commit()
        session.refresh(client)
        return jsonify({"id": client.id}), 201
    finally:
        session.close()


@clients_bp.route("/<int:client_id>", methods=["GET"])
@requires_auth()
async def get_client(client_id):
    user = request.user
    session = SessionLocal()
    try:
        client_query = session.query(Client).filter(
            Client.id == client_id,
            Client.tenant_id == user.tenant_id,
            Client.deleted_at == None,
        )

        if not any(role.name == "admin" for role in user.roles):
            client_query = client_query.filter(
                or_(
                    Client.created_by == user.id,
                    Client.assigned_to == user.id
                )
            )

        client = client_query.first()
        if not client:
            return jsonify({"error": "Client not found"}), 404

        log = ActivityLog(
            tenant_id=user.tenant_id,
            user_id=user.id,
            action=ActivityType.viewed,
            entity_type="client",
            entity_id=client.id,
            description=f"Viewed client '{client.name}'"
        )
        session.add(log)
        session.commit()

        response = jsonify({
            "id": client.id,
            "name": client.name,
            "email": client.email,
            "phone": client.phone,
            "phone_label": client.phone_label,
            "secondary_phone": client.secondary_phone,
            "secondary_phone_label": client.secondary_phone_label,
            "address": client.address,
            "contact_person": client.contact_person,
            "contact_title": client.contact_title,
            "city": client.city,
            "state": client.state,
            "zip": client.zip,
            "notes": client.notes,
            "type": client.type,
            "created_at": client.created_at.isoformat() + "Z",
            "contacts": [c.to_dict() for c in client.contacts] if client.contacts else []
        })

        response.headers["Cache-Control"] = "no-store"
        return response
    finally:
        session.close()


@clients_bp.route("/<int:client_id>", methods=["PUT"])
@requires_auth()
async def update_client(client_id):
    user = request.user
    data = await request.get_json()
    session = SessionLocal()
    try:
        client_query = session.query(Client).filter(
            Client.id == client_id,
            Client.tenant_id == user.tenant_id,
            Client.deleted_at == None
        )

        if not any(role.name == "admin" for role in user.roles):
            client_query = client_query.filter(
                or_(
                    Client.created_by == user.id,
                    Client.assigned_to == user.id
                )
            )

        client = client_query.first()
        if not client:
            return jsonify({"error": "Client not found"}), 404

        for field in [
            "name", "contact_person", "contact_title", "email", "phone_label", 
            "secondary_phone_label", "address", "city", "state", "zip", "notes"
        ]:
            if field in data:
                setattr(client, field, data[field] or None)
        if "phone" in data:
            client.phone = clean_phone_number(data["phone"]) if data["phone"] else None
        if "secondary_phone" in data:
            client.secondary_phone = clean_phone_number(data["secondary_phone"]) if data["secondary_phone"] else None
        if "type" in data and data["type"] in TYPE_OPTIONS:
            client.type = data["type"]

        client.updated_by = user.id
        client.updated_at = datetime.utcnow()

        session.commit()
        session.refresh(client)
        return jsonify({"id": client.id})
    finally:
        session.close()


@clients_bp.route("/<int:client_id>", methods=["DELETE"])
@requires_auth()
async def delete_client(client_id):
    user = request.user
    session = SessionLocal()
    try:
        client_query = session.query(Client).filter(
            Client.id == client_id,
            Client.tenant_id == user.tenant_id,
            Client.deleted_at == None
        )

        if not any(role.name == "admin" for role in user.roles):
            client_query = client_query.filter(
                or_(
                    Client.created_by == user.id,
                    Client.assigned_to == user.id
                )
            )

        client = client_query.first()
        if not client:
            return jsonify({"error": "Client not found"}), 404

        client.deleted_at = datetime.utcnow()
        client.deleted_by = user.id
        session.commit()
        return jsonify({"message": "Client soft-deleted successfully"})
    finally:
        session.close()




@clients_bp.route("/<int:client_id>/assign", methods=["PUT"])
@requires_auth(roles=["admin"])
async def assign_client(client_id):
    user = request.user
    data = await request.get_json()
    assigned_to = data.get("assigned_to")

    if not assigned_to:
        return jsonify({"error": "Missing assigned_to"}), 400

    session = SessionLocal()
    try:
        client = session.query(Client).filter(
            Client.id == client_id,
            Client.tenant_id == user.tenant_id,
            Client.deleted_at == None
        ).first()

        if not client:
            return jsonify({"error": "Client not found"}), 404

        # Optional: validate user exists and is active
        assigned_user = session.query(User).filter(
            User.id == assigned_to,
            User.tenant_id == user.tenant_id,
            User.is_active == True
        ).first()

        if not assigned_user:
            return jsonify({"error": "Assigned user not found or inactive"}), 400

        client.assigned_to = assigned_to
        client.updated_by = user.id
        client.updated_at = datetime.utcnow()

        await send_assignment_notification(
            to_email=assigned_user.email,
            entity_type="client",
            entity_name=client.name,
            assigned_by=user.email
        )

        session.commit()
        return jsonify({"message": "Client assigned successfully"})
    finally:
        session.close()


@clients_bp.route("/all", methods=["GET"])
@requires_auth(roles=["admin"])
async def list_all_clients():
    user = request.user
    session = SessionLocal()
    try:
        # Get pagination parameters
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 20))
        sort_order = request.args.get("sort", "newest")
        user_email = request.args.get("user_email")  # Filter by specific user
        activity_filter = request.args.get("activity_filter", "all")  # NEW: Activity filter
        
        # Validate sort order
        if sort_order not in ["newest", "oldest", "alphabetical", "activity"]:
            sort_order = "newest"

        query = session.query(Client).options(
            joinedload(Client.assigned_user),
            joinedload(Client.created_by_user)
        ).filter(
            Client.tenant_id == user.tenant_id,
            Client.deleted_at == None
        )

        # Filter by user if specified
        if user_email:
            query = query.filter(
                or_(
                    Client.assigned_user.has(User.email == user_email),
                    Client.created_by_user.has(User.email == user_email)
                )
            )

        # Apply activity filtering (same logic as main list)
        if activity_filter == "active":
            thirty_days_ago = datetime.utcnow() - timedelta(days=30)
            query = query.join(Interaction, Client.id == Interaction.client_id).filter(
                Interaction.contact_date >= thirty_days_ago
            ).distinct()
        elif activity_filter == "inactive":
            ninety_days_ago = datetime.utcnow() - timedelta(days=90)
            recent_interaction_clients = session.query(Interaction.client_id).filter(
                Interaction.client_id != None,
                Interaction.contact_date >= ninety_days_ago
            ).distinct().subquery()
            query = query.outerjoin(
                recent_interaction_clients, 
                Client.id == recent_interaction_clients.c.client_id
            ).filter(recent_interaction_clients.c.client_id == None)
        elif activity_filter == "new":
            seven_days_ago = datetime.utcnow() - timedelta(days=7)
            query = query.filter(Client.created_at >= seven_days_ago)

        # Apply sorting
        if sort_order == "newest":
            query = query.order_by(Client.created_at.desc())
        elif sort_order == "oldest":
            query = query.order_by(Client.created_at.asc())
        elif sort_order == "alphabetical":
            query = query.order_by(Client.name.asc())
        elif sort_order == "activity":
            query = query.outerjoin(Interaction, Client.id == Interaction.client_id)\
                         .group_by(Client.id)\
                         .order_by(desc(func.max(Interaction.contact_date)))

        total = query.count()
        clients = query.offset((page - 1) * per_page).limit(per_page).all()

        # Get interaction statistics
        client_ids = [c.id for c in clients]
        interaction_stats = {}
        
        if client_ids:
            interaction_data = session.query(
                Interaction.client_id,
                func.count(Interaction.id).label('interaction_count'),
                func.max(Interaction.contact_date).label('last_interaction_date')
            ).filter(
                Interaction.client_id.in_(client_ids)
            ).group_by(Interaction.client_id).all()
            
            for data in interaction_data:
                interaction_stats[data.client_id] = {
                    'interaction_count': data.interaction_count,
                    'last_interaction_date': data.last_interaction_date.isoformat() + "Z" if data.last_interaction_date else None
                }

        response_data = {
            "clients": [
                {
                    "id": c.id,
                    "name": c.name,
                    "email": c.email,
                    "phone": c.phone,
                    "phone_label": c.phone_label,
                    "secondary_phone": c.secondary_phone,
                    "secondary_phone_label": c.secondary_phone_label,
                    "contact_person": c.contact_person,
                    "contact_title": c.contact_title,
                    "type": c.type,
                    "created_by": c.created_by,
                    "created_by_name": c.created_by_user.email if c.created_by_user else None,
                    "assigned_to_name": (
                        c.assigned_user.email if c.assigned_user
                        else c.created_by_user.email if c.created_by_user
                        else None
                    ),
                    "created_at": c.created_at.isoformat() + "Z" if c.created_at else None,
                    # NEW: Interaction statistics
                    "interaction_count": interaction_stats.get(c.id, {}).get('interaction_count', 0),
                    "last_interaction_date": interaction_stats.get(c.id, {}).get('last_interaction_date'),
                } for c in clients
            ],
            "total": total,
            "page": page,
            "per_page": per_page,
            "sort_order": sort_order,
            "user_email": user_email,
            "activity_filter": activity_filter  # NEW: Include filter in response
        }

        response = jsonify(response_data)
        response.headers["Cache-Control"] = "no-store"
        return response
    finally:
        session.close()


@clients_bp.route("/assigned", methods=["GET"])
@requires_auth()
async def list_assigned_clients():
    user = request.user
    session = SessionLocal()
    try:
        clients = session.query(Client).options(
            joinedload(Client.assigned_user)
        ).filter(
            Client.tenant_id == user.tenant_id,
            Client.assigned_to == user.id,
            Client.deleted_at == None
        ).all()

        return jsonify([
            {
                "id": c.id,
                "name": c.name,
                "email": c.email,
                "phone": c.phone,
                "phone_label": c.phone_label,
                "secondary_phone": c.secondary_phone,
                "secondary_phone_label": c.secondary_phone_label,
                "contact_person": c.contact_person,
                "contact_title": c.contact_title,
                "type": c.type,
                "assigned_to_name": c.assigned_user.email if c.assigned_user else None,
            } for c in clients
        ])
    finally:
        session.close()


@clients_bp.route("/trash", methods=["GET"])
@requires_auth()
async def list_trashed_clients():
    user = request.user
    session = SessionLocal()
    try:
        if not any(role.name == "admin" for role in user.roles):
            trashed = session.query(Client).filter(
                Client.tenant_id == user.tenant_id,
                Client.deleted_at != None,
                or_(
                    Client.created_by == user.id,
                    Client.assigned_to == user.id
                )
            ).order_by(Client.deleted_at.desc()).all()
        else:
            trashed = session.query(Client).filter(
                Client.tenant_id == user.tenant_id,
                Client.deleted_at != None
            ).order_by(Client.deleted_at.desc()).all()

        return jsonify([
            {
                "id": c.id,
                "name": c.name,
                "deleted_at": c.deleted_at.isoformat() + "Z",
                "deleted_by": c.deleted_by
            } for c in trashed
        ])
    finally:
        session.close()



@clients_bp.route("/<int:client_id>/restore", methods=["PUT"])
@requires_auth(roles=[])
async def restore_client(client_id):
    user = request.user
    session = SessionLocal()
    try:
        client = session.query(Client).filter(
            Client.id == client_id,
            Client.tenant_id == user.tenant_id,
            Client.deleted_at != None
        ).first()

        if not client:
            return jsonify({"error": "Client not found or not deleted"}), 404

        client.deleted_at = None
        client.deleted_by = None
        session.commit()
        return jsonify({"message": "Client restored successfully"})
    finally:
        session.close()


@clients_bp.route("/<int:client_id>/purge", methods=["DELETE"])
@requires_auth(roles=["admin"])
async def purge_client(client_id):
    user = request.user
    session = SessionLocal()
    try:
        client = session.query(Client).filter(
            Client.id == client_id,
            Client.tenant_id == user.tenant_id,
            Client.deleted_at != None
        ).first()

        if not client:
            return jsonify({"error": "Client not found or not eligible for purge"}), 404

        session.delete(client)
        session.commit()
        return jsonify({"message": "Client permanently deleted"}), 200
    finally:
        session.close()


@clients_bp.route("/bulk-delete", methods=["POST"])
@requires_auth(roles=["admin"])
async def bulk_delete_clients():
    user = request.user
    data = await request.get_json()
    client_ids = data.get("client_ids", [])

    if not client_ids or not isinstance(client_ids, list):
        return jsonify({"error": "No client IDs provided"}), 400

    session = SessionLocal()
    try:
        updated_count = session.query(Client).filter(
            Client.tenant_id == user.tenant_id,
            Client.id.in_(client_ids),
            Client.deleted_at == None
        ).update(
            {Client.deleted_at: datetime.utcnow(), Client.deleted_by: user.id},
            synchronize_session=False
        )
        session.commit()
        return jsonify({"message": f"{updated_count} client(s) deleted"})
    finally:
        session.close()

