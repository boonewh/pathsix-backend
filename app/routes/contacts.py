from quart import Blueprint, request, jsonify
from pydantic import ValidationError
from app.models import Contact
from app.database import SessionLocal
from app.utils.auth_utils import requires_auth
from app.utils.phone_utils import clean_phone_number
from app.schemas.contacts import ContactCreateSchema, ContactUpdateSchema

contacts_bp = Blueprint("contacts", __name__, url_prefix="/api/contacts")


@contacts_bp.route("", methods=["GET"])
@contacts_bp.route("/", methods=["GET"])
@requires_auth()
async def list_contacts():
    user = request.user
    client_id = request.args.get("client_id")
    lead_id = request.args.get("lead_id")

    session = SessionLocal()
    try:
        query = session.query(Contact).filter(Contact.tenant_id == user.tenant_id)

        if client_id:
            query = query.filter(Contact.client_id == client_id)
        elif lead_id:
            query = query.filter(Contact.lead_id == lead_id)
        else:
            return jsonify([])

        contacts = query.all()

        return jsonify([
            {
                "id": c.id,
                "first_name": c.first_name,
                "last_name": c.last_name,
                "title": c.title,
                "email": c.email,
                "phone": c.phone,
                "phone_label": c.phone_label,
                "secondary_phone": c.secondary_phone,
                "secondary_phone_label": c.secondary_phone_label,
                "notes": c.notes,
            } for c in contacts
        ])
    finally:
        session.close()


@contacts_bp.route("", methods=["POST"])
@contacts_bp.route("/", methods=["POST"])
@requires_auth()
async def create_contact():
    user = request.user
    raw_data = await request.get_json()
    
    # Validate input using Pydantic schema
    try:
        data = ContactCreateSchema(**raw_data)
    except ValidationError as e:
        return jsonify({
            "error": "Validation failed",
            "details": e.errors()
        }), 400

    session = SessionLocal()
    try:
        contact = Contact(
            tenant_id=user.tenant_id,
            client_id=data.client_id,
            lead_id=data.lead_id,
            first_name=data.first_name,
            last_name=data.last_name,
            title=data.title,
            email=str(data.email) if data.email else None,
            phone=clean_phone_number(data.phone) if data.phone else None,
            phone_label=data.phone_label,
            secondary_phone=clean_phone_number(data.secondary_phone) if data.secondary_phone else None,
            secondary_phone_label=data.secondary_phone_label,
            notes=data.notes,
        )
        session.add(contact)
        session.commit()
        session.refresh(contact)

        return jsonify({"id": contact.id}), 201
    finally:
        session.close()


@contacts_bp.route("/<int:contact_id>", methods=["PUT"])
@requires_auth()
async def update_contact(contact_id):
    user = request.user
    raw_data = await request.get_json()
    
    # Validate input using Pydantic schema
    try:
        data = ContactUpdateSchema(**raw_data)
    except ValidationError as e:
        return jsonify({
            "error": "Validation failed",
            "details": e.errors()
        }), 400

    session = SessionLocal()
    try:
        contact = session.query(Contact).filter(
            Contact.id == contact_id,
            Contact.tenant_id == user.tenant_id
        ).first()

        if not contact:
            return jsonify({"error": "Contact not found"}), 404

        # Update fields with validated data
        update_data = data.model_dump(exclude_unset=True)
        
        for field, value in update_data.items():
            if field == "phone":
                contact.phone = clean_phone_number(value) if value else None
            elif field == "secondary_phone":
                contact.secondary_phone = clean_phone_number(value) if value else None
            elif field == "email":
                contact.email = str(value) if value else None
            else:
                setattr(contact, field, value)

        session.commit()
        return jsonify({"message": "Contact updated"})
    finally:
        session.close()


@contacts_bp.route("/<int:contact_id>", methods=["DELETE"])
@requires_auth()
async def delete_contact(contact_id):
    user = request.user
    session = SessionLocal()
    try:
        contact = session.query(Contact).filter(
            Contact.id == contact_id,
            Contact.tenant_id == user.tenant_id
        ).first()

        if not contact:
            return jsonify({"error": "Contact not found"}), 404

        session.delete(contact)
        session.commit()
        return jsonify({"message": "Contact deleted"})
    finally:
        session.close()
