from quart import Blueprint, request, jsonify, Response
from pydantic import ValidationError
from app.database import SessionLocal
from app.utils.auth_utils import requires_auth
from app.services.interactions import InteractionService, RecordNotFound
from app.schemas.interactions import InteractionCreateSchema, InteractionUpdateSchema

interactions_bp = Blueprint("interactions", __name__, url_prefix="/api/interactions")


@interactions_bp.route("", methods=["GET"])
@interactions_bp.route("/", methods=["GET"])
@requires_auth()
async def list_interactions():
    with SessionLocal() as session:
        try:
            service = InteractionService(session, request.principal)
            result = service.list_visible(
                client_id=request.args.get("client_id") or None,
                lead_id=request.args.get("lead_id") or None,
                project_id=request.args.get("project_id") or None,
                page=int(request.args.get("page", 1)),
                per_page=int(request.args.get("per_page", 10)),
                sort_order=request.args.get("sort", "newest"),
            )
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store"
            return response
        except RecordNotFound as exc:
            return jsonify({"error": str(exc)}), 404
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 403
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400


@interactions_bp.route("", methods=["POST"])
@interactions_bp.route("/", methods=["POST"])
@requires_auth()
async def create_interaction():
    raw = await request.get_json()
    if not isinstance(raw, dict):
        return jsonify({"error": "Invalid request body"}), 400
    try:
        data = InteractionCreateSchema(**raw)
    except ValidationError as exc:
        return jsonify({"error": "Validation failed", "details": exc.errors()}), 400
    with SessionLocal() as session:
        try:
            service = InteractionService(session, request.principal)
            result = service.create(data)
            session.commit()
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store"
            return response, 201
        except RecordNotFound as exc:
            return jsonify({"error": str(exc)}), 404
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 403
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400


@interactions_bp.route("/<int:interaction_id>", methods=["PUT"])
@requires_auth()
async def update_interaction(interaction_id):
    raw = await request.get_json()
    if not isinstance(raw, dict):
        return jsonify({"error": "Invalid request body"}), 400
    try:
        data = InteractionUpdateSchema(**raw)
    except ValidationError as exc:
        return jsonify({"error": "Validation failed", "details": exc.errors()}), 400
    with SessionLocal() as session:
        try:
            service = InteractionService(session, request.principal)
            result = service.update(interaction_id, data)
            session.commit()
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store"
            return response
        except RecordNotFound as exc:
            return jsonify({"error": str(exc)}), 404
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 403
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400


@interactions_bp.route("/<int:interaction_id>", methods=["DELETE"])
@requires_auth()
async def delete_interaction(interaction_id):
    with SessionLocal() as session:
        try:
            service = InteractionService(session, request.principal)
            result = service.delete(interaction_id)
            session.commit()
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store"
            return response
        except RecordNotFound as exc:
            return jsonify({"error": str(exc)}), 404
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 403
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400


@interactions_bp.route("/transfer", methods=["POST"])
@requires_auth()
async def transfer_interactions():
    raw = await request.get_json()
    if not isinstance(raw, dict):
        return jsonify({"error": "Invalid request body"}), 400
    with SessionLocal() as session:
        try:
            service = InteractionService(session, request.principal)
            result = service.transfer(raw.get("from_lead_id"), raw.get("to_client_id"))
            session.commit()
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store"
            return response
        except RecordNotFound as exc:
            return jsonify({"error": str(exc)}), 404
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 403
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400


@interactions_bp.route("/<int:interaction_id>/calendar.ics", methods=["GET"])
@requires_auth()
async def get_interaction_ics(interaction_id):
    with SessionLocal() as session:
        try:
            service = InteractionService(session, request.principal)
            result = service.calendar(interaction_id)
            return Response(
                result,
                content_type="text/calendar",
                headers={
                    "Cache-Control": "no-store",
                    "Content-Disposition": f"attachment; filename=interaction-{interaction_id}.ics",
                },
            )
        except RecordNotFound as exc:
            return jsonify({"error": str(exc)}), 404
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 403
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400


@interactions_bp.route("/<int:interaction_id>/complete", methods=["PUT"])
@requires_auth()
async def complete_interaction(interaction_id):
    with SessionLocal() as session:
        try:
            service = InteractionService(session, request.principal)
            result = service.complete(interaction_id)
            session.commit()
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store"
            return response
        except RecordNotFound as exc:
            return jsonify({"error": str(exc)}), 404
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 403
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400


@interactions_bp.route("/all", methods=["GET"])
@requires_auth(roles=["admin"])
async def list_all_interactions_admin():
    with SessionLocal() as session:
        try:
            service = InteractionService(session, request.principal)
            result = service.list_all(
                page=int(request.args.get("page", 1)),
                per_page=int(request.args.get("per_page", 20)),
                sort_order=request.args.get("sort", "newest"),
                user_email=request.args.get("user_email"),
            )
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store"
            return response
        except RecordNotFound as exc:
            return jsonify({"error": str(exc)}), 404
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 403
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
