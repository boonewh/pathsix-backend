from quart import Blueprint, request, jsonify
from pydantic import ValidationError
from app.database import SessionLocal
from app.utils.auth_utils import requires_auth
from app.schemas.contacts import ContactCreateSchema, ContactUpdateSchema
from app.services.contacts import ContactService, RecordNotFound

contacts_bp = Blueprint("contacts", __name__, url_prefix="/api/contacts")


@contacts_bp.route("", methods=["GET"])
@contacts_bp.route("/", methods=["GET"])
@requires_auth()
async def list_contacts():
    with SessionLocal() as session:
        try:
            return jsonify(ContactService(session, request.principal).list_for_parent(
                client_id=request.args.get('client_id') or None,
                lead_id=request.args.get('lead_id') or None))
        except RecordNotFound as exc:
            return jsonify({'error': str(exc)}), 404
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400


@contacts_bp.route("", methods=["POST"])
@contacts_bp.route("/", methods=["POST"])
@requires_auth()
async def create_contact():
    raw_data = await request.get_json()
    if not isinstance(raw_data, dict):
        return jsonify({'error': 'Invalid request body'}), 400
    try:
        data = ContactCreateSchema(**raw_data)
    except ValidationError as exc:
        return jsonify({'error': 'Validation failed', 'details': exc.errors()}), 400
    with SessionLocal() as session:
        try:
            contact_id = ContactService(session, request.principal).create(data)
            session.commit()
            return jsonify({'id': contact_id}), 201
        except RecordNotFound as exc:
            return jsonify({'error': str(exc)}), 404
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400


@contacts_bp.route("/<int:contact_id>", methods=["PUT"])
@requires_auth()
async def update_contact(contact_id):
    raw_data = await request.get_json()
    if not isinstance(raw_data, dict):
        return jsonify({'error': 'Invalid request body'}), 400
    try:
        data = ContactUpdateSchema(**raw_data)
    except ValidationError as exc:
        return jsonify({'error': 'Validation failed', 'details': exc.errors()}), 400
    with SessionLocal() as session:
        try:
            ContactService(session, request.principal).update(contact_id, data)
            session.commit()
            return jsonify({'message': 'Contact updated'})
        except RecordNotFound as exc:
            return jsonify({'error': str(exc)}), 404
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400


@contacts_bp.route("/<int:contact_id>", methods=["DELETE"])
@requires_auth()
async def delete_contact(contact_id):
    with SessionLocal() as session:
        try:
            ContactService(session, request.principal).delete(contact_id)
            session.commit()
            return jsonify({'message': 'Contact deleted'})
        except RecordNotFound as exc:
            return jsonify({'error': str(exc)}), 404
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400
