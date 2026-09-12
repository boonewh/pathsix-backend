from quart import Blueprint, request, jsonify, Response
from pydantic import ValidationError
from app.database import SessionLocal
from app.utils.auth_utils import requires_auth
from app.services.projects import ProjectService, RecordNotFound
from app.schemas.projects import (
    ProjectCreateSchema,
    ProjectUpdateSchema,
    ProjectAssignSchema,
)
from app.utils.email_utils import send_assignment_notification

projects_bp = Blueprint("projects", __name__, url_prefix="/api/projects")


@projects_bp.route("", methods=["GET"])
@projects_bp.route("/", methods=["GET"])
@requires_auth()
async def list_projects():
    with SessionLocal() as session:
        try:
            service = ProjectService(session, request.principal)
            result = service.list_mine(
                page=int(request.args.get("page", 1)),
                per_page=int(request.args.get("per_page", 20)),
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


@projects_bp.route("/<int:project_id>", methods=["GET"])
@requires_auth()
async def get_project(project_id):
    with SessionLocal() as session:
        try:
            service = ProjectService(session, request.principal)
            result = service.detail(project_id)
            service.record_view(project_id)
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


@projects_bp.route("", methods=["POST"])
@projects_bp.route("/", methods=["POST"])
@requires_auth()
async def create_project():
    raw = await request.get_json()
    if not isinstance(raw, dict):
        return jsonify({"error": "Invalid request body"}), 400
    try:
        data = ProjectCreateSchema(**raw)
    except ValidationError as exc:
        return jsonify({"error": "Validation failed", "details": exc.errors()}), 400
    with SessionLocal() as session:
        try:
            service = ProjectService(session, request.principal)
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


@projects_bp.route("/<int:project_id>", methods=["PUT"])
@requires_auth()
async def update_project(project_id):
    raw = await request.get_json()
    if not isinstance(raw, dict):
        return jsonify({"error": "Invalid request body"}), 400
    try:
        data = ProjectUpdateSchema(**raw)
    except ValidationError as exc:
        return jsonify({"error": "Validation failed", "details": exc.errors()}), 400
    with SessionLocal() as session:
        try:
            service = ProjectService(session, request.principal)
            result = service.update(project_id, data)
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


@projects_bp.route("/<int:project_id>/assign", methods=["PUT"])
@requires_auth(roles=["admin"])
async def assign_project(project_id):
    raw = await request.get_json()
    if not isinstance(raw, dict):
        return jsonify({"error": "Invalid request body"}), 400
    try:
        data = ProjectAssignSchema(**raw)
    except ValidationError as exc:
        return jsonify({"error": "Validation failed", "details": exc.errors()}), 400
    with SessionLocal() as session:
        try:
            service = ProjectService(session, request.principal)
            result = service.assign(project_id, data)
            session.commit()
        except RecordNotFound as exc:
            return jsonify({"error": str(exc)}), 404
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 403
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
    assigned_to = result.pop("assigned_to")
    try:
        await send_assignment_notification(**result, assigned_by=request.user.email)
    except Exception:
        pass
    return jsonify(
        {"message": "Project assigned successfully", "assigned_to": assigned_to}
    )


@projects_bp.route("/<int:project_id>/interactions", methods=["GET"])
@requires_auth()
async def get_project_interactions(project_id):
    with SessionLocal() as session:
        try:
            service = ProjectService(session, request.principal)
            result = service.interaction_link(project_id)
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store"
            return response
        except RecordNotFound as exc:
            return jsonify({"error": str(exc)}), 404
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 403
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400


@projects_bp.route("/all", methods=["GET"])
@requires_auth(roles=["admin"])
async def list_all_projects():
    with SessionLocal() as session:
        try:
            service = ProjectService(session, request.principal)
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


@projects_bp.route("/by-client/<int:client_id>", methods=["GET"])
@requires_auth()
async def list_projects_by_client(client_id):
    with SessionLocal() as session:
        try:
            service = ProjectService(session, request.principal)
            result = service.by_client(client_id)
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store"
            return response
        except RecordNotFound as exc:
            return jsonify({"error": str(exc)}), 404
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 403
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400


@projects_bp.route("/by-lead/<int:lead_id>", methods=["GET"])
@requires_auth()
async def list_projects_by_lead(lead_id):
    with SessionLocal() as session:
        try:
            service = ProjectService(session, request.principal)
            result = service.by_lead(lead_id)
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store"
            return response
        except RecordNotFound as exc:
            return jsonify({"error": str(exc)}), 404
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 403
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400


@projects_bp.route("/<int:project_id>", methods=["DELETE"])
@requires_auth()
async def delete_project(project_id):
    with SessionLocal() as session:
        try:
            service = ProjectService(session, request.principal)
            result = service.delete(project_id)
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


@projects_bp.route("/trash", methods=["GET"])
@requires_auth()
async def list_trashed_projects():
    with SessionLocal() as session:
        try:
            service = ProjectService(session, request.principal)
            result = service.list_trash()
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store"
            return response
        except RecordNotFound as exc:
            return jsonify({"error": str(exc)}), 404
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 403
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400


@projects_bp.route("/<int:project_id>/restore", methods=["PUT"])
@requires_auth()
async def restore_project(project_id):
    with SessionLocal() as session:
        try:
            service = ProjectService(session, request.principal)
            result = service.restore(project_id)
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


@projects_bp.route("/<int:project_id>/purge", methods=["DELETE"])
@requires_auth(roles=["admin"])
async def purge_project(project_id):
    with SessionLocal() as session:
        try:
            service = ProjectService(session, request.principal)
            result = service.purge(project_id)
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


@projects_bp.route("/bulk-delete", methods=["POST"])
@requires_auth(roles=["admin"])
async def bulk_delete_projects():
    raw = await request.get_json()
    if not isinstance(raw, dict):
        return jsonify({"error": "Invalid request body"}), 400
    with SessionLocal() as session:
        try:
            service = ProjectService(session, request.principal)
            result = service.bulk_delete(raw.get("project_ids"))
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


@projects_bp.route("/bulk-purge", methods=["DELETE", "POST"])
@requires_auth(roles=["admin"])
async def bulk_purge_projects():
    raw = await request.get_json()
    if not isinstance(raw, dict):
        return jsonify({"error": "Invalid request body"}), 400
    with SessionLocal() as session:
        try:
            service = ProjectService(session, request.principal)
            result = service.bulk_purge(raw.get("project_ids"))
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
