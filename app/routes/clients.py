from app.routes.purge import purge_response
from app.services.clients import ClientService, RecordNotFound
from quart import Blueprint, request, jsonify
from pydantic import ValidationError
from app.database import SessionLocal
from app.utils.auth_utils import requires_auth
from app.utils.email_utils import send_assignment_notification
from app.schemas.clients import ClientCreateSchema, ClientUpdateSchema, ClientAssignSchema

clients_bp = Blueprint("clients", __name__, url_prefix="/api/clients")

@clients_bp.route("", methods=["GET"])
@clients_bp.route("/", methods=["GET"])
@requires_auth()
async def list_clients():
    with SessionLocal() as session:
        try:
            result = ClientService(session, request.principal).list_mine(
                page=int(request.args.get("page", 1)),
                per_page=int(request.args.get("per_page", 20)),
                sort_order=request.args.get("sort", "newest"),
                activity_filter=request.args.get("activity_filter", "all"),
            )
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store"
            return response
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400


@clients_bp.route("", methods=["POST"])
@clients_bp.route("/", methods=["POST"])
@requires_auth()
async def create_client():
    raw_data = await request.get_json()
    try:
        data = ClientCreateSchema(**raw_data)
    except ValidationError as exc:
        return jsonify({"error": "Validation failed", "details": exc.errors()}), 400
    with SessionLocal() as session:
        try:
            client_id = ClientService(session, request.principal).create(data)
            session.commit()
            return jsonify({"id": client_id}), 201
        except RecordNotFound as exc:
            return jsonify({"error": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400


@clients_bp.route("/<int:client_id>", methods=["GET"])
@requires_auth()
async def get_client(client_id):
    with SessionLocal() as session:
        try:
            service = ClientService(session, request.principal)
            data = service.detail(client_id)
            service.record_view(client_id)
            session.commit()
            response = jsonify(data)
            response.headers["Cache-Control"] = "no-store"
            return response
        except RecordNotFound as exc:
            return jsonify({"error": str(exc)}), 404


@clients_bp.route("/<int:client_id>", methods=["PUT"])
@requires_auth()
async def update_client(client_id):
    raw_data = await request.get_json()
    try:
        data = ClientUpdateSchema(**raw_data)
    except ValidationError as exc:
        return jsonify({"error": "Validation failed", "details": exc.errors()}), 400
    with SessionLocal() as session:
        try:
            result_id = ClientService(session, request.principal).update(client_id, data)
            session.commit()
            return jsonify({"id": result_id})
        except RecordNotFound as exc:
            return jsonify({"error": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400


@clients_bp.route("/<int:client_id>", methods=["DELETE"])
@requires_auth()
async def delete_client(client_id):
    with SessionLocal() as session:
        try:
            deleted = ClientService(session, request.principal).delete(client_id)
            session.commit()
            return jsonify({"message": "Client soft-deleted successfully" if deleted else "Client already deleted"})
        except RecordNotFound as exc:
            return jsonify({"error": str(exc)}), 404


@clients_bp.route("/<int:client_id>/assign", methods=["PUT"])
@requires_auth(roles=["admin"])
async def assign_client(client_id):
    raw_data = await request.get_json()
    if not isinstance(raw_data, dict):
        return jsonify({"error": "Invalid request body"}), 400
    try:
        data = ClientAssignSchema(**raw_data)
    except ValidationError as exc:
        return jsonify({"error": "Validation failed", "details": exc.errors()}), 400
    with SessionLocal() as session:
        try:
            notification = ClientService(session, request.principal).assign(client_id, data)
            session.commit()
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 403
        except RecordNotFound as exc:
            return jsonify({"error": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
    # Match lead assignment: mail failure cannot undo a committed assignment.
    try:
        await send_assignment_notification(**notification, assigned_by=request.user.email)
    except Exception:
        pass
    return jsonify({"message": "Client assigned successfully"})


@clients_bp.route("/all", methods=["GET"])
@requires_auth(roles=["admin"])
async def list_all_clients():
    with SessionLocal() as session:
        try:
            result = ClientService(session, request.principal).list_all(
                page=int(request.args.get("page", 1)),
                per_page=int(request.args.get("per_page", 20)),
                sort_order=request.args.get("sort", "newest"),
                user_email=request.args.get("user_email"),
                activity_filter=request.args.get("activity_filter", "all"),
            )
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store"
            return response
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 403
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400


@clients_bp.route("/assigned", methods=["GET"])
@requires_auth()
async def list_assigned_clients():
    with SessionLocal() as session:
        return jsonify(ClientService(session, request.principal).list_assigned())


@clients_bp.route("/trash", methods=["GET"])
@requires_auth()
async def list_trashed_clients():
    with SessionLocal() as session:
        return jsonify(ClientService(session, request.principal).list_trash())


@clients_bp.route("/<int:client_id>/restore", methods=["PUT"])
@requires_auth()
async def restore_client(client_id):
    with SessionLocal() as session:
        try:
            ClientService(session, request.principal).restore(client_id)
            session.commit()
            return jsonify({"message": "Client restored successfully"})
        except RecordNotFound as exc:
            return jsonify({"error": str(exc)}), 404


@clients_bp.route("/<int:client_id>/purge", methods=["DELETE"])
@requires_auth(roles=["admin"])
async def purge_client(client_id):
    return await purge_response(SessionLocal, "clients", client_id)

@clients_bp.route("/bulk-delete", methods=["POST"])
@requires_auth(roles=["admin"])
async def bulk_delete_clients():
    data = await request.get_json()
    with SessionLocal() as session:
        try:
            count = ClientService(session, request.principal).bulk_delete(
                data.get("client_ids") if isinstance(data, dict) else None,
            )
            session.commit()
            return jsonify({"message": f"{count} client(s) deleted"})
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 403
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400


@clients_bp.route("/bulk-purge", methods=["DELETE", "POST"])
@requires_auth(roles=["admin"])
async def bulk_purge_clients():
    return await purge_response(SessionLocal, "clients")
