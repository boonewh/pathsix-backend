from quart import Blueprint, request, jsonify
from app.models import Contact
from app.database import SessionLocal
from app.utils.auth_utils import requires_auth
from app.utils.phone_utils import clean_phone_number

contacts_bp = Blueprint("contacts", __name__, url_prefix="/api/contacts")


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


@contacts_bp.route("/", methods=["POST"])
@requires_auth()
async def create_contact():
    user = request.user
    data = await request.get_json()

    session = SessionLocal()
    try:
        contact = Contact(
            tenant_id=user.tenant_id,
            client_id=data.get("client_id"),
            lead_id=data.get("lead_id"),
            first_name=data.get("first_name"),
            last_name=data.get("last_name"),
            title=data.get("title"),
            email=data.get("email"),
            phone=clean_phone_number(data.get("phone")) if data.get("phone") else None,
            phone_label=data.get("phone_label"),
            secondary_phone=clean_phone_number(data.get("secondary_phone")) if data.get("secondary_phone") else None,
            secondary_phone_label=data.get("secondary_phone_label"),
            notes=data.get("notes"),
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
    data = await request.get_json()

    session = SessionLocal()
    try:
        contact = session.query(Contact).filter(
            Contact.id == contact_id,
            Contact.tenant_id == user.tenant_id
        ).first()

        if not contact:
            return jsonify({"error": "Contact not found"}), 404

        for field in [
            "first_name", "last_name", "title", "email",
            "phone_label", "secondary_phone_label", "notes"
        ]:
            if field in data:
                setattr(contact, field, data[field])

        # Handle phone fields separately with cleaning
        if "phone" in data:
            contact.phone = clean_phone_number(data["phone"]) if data["phone"] else None
        if "secondary_phone" in data:
            contact.secondary_phone = clean_phone_number(data["secondary_phone"]) if data["secondary_phone"] else None

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
