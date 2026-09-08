from quart import Blueprint, request, jsonify
from pydantic import ValidationError
from app.database import SessionLocal
from app.services.leads import LeadService, RecordNotFound
from app.utils.auth_utils import requires_auth
from app.utils.email_utils import send_assignment_notification
from app.schemas.leads import LeadCreateSchema, LeadUpdateSchema, LeadAssignSchema

leads_bp = Blueprint("leads", __name__, url_prefix="/api/leads")


@leads_bp.route("", methods=["GET"])
@leads_bp.route("/", methods=["GET"])
@requires_auth()
async def list_leads():
    with SessionLocal() as session:
        try:
            result = LeadService(session, request.principal).list_mine(page=int(request.args.get("page", 1)), per_page=int(request.args.get("per_page", 20)), sort_order=request.args.get("sort", "newest"))
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store"
            return response
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 403
        except RecordNotFound as exc:
            return jsonify({"error": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400


@leads_bp.route("", methods=["POST"])
@leads_bp.route("/", methods=["POST"])
@requires_auth()
async def create_lead():
    raw_data = await request.get_json()
    if not isinstance(raw_data, dict):
        return jsonify({"error": "Invalid request body"}), 400
    try:
        data = LeadCreateSchema(**raw_data)
    except ValidationError as exc:
        return jsonify({"error": "Validation failed", "details": exc.errors()}), 400
    with SessionLocal() as session:
        lead_id = LeadService(session, request.principal).create(data)
        session.commit()
        return jsonify({"id": lead_id}), 201


@leads_bp.route("/<int:lead_id>", methods=["GET"])
@requires_auth()
async def get_lead(lead_id):
    with SessionLocal() as session:
        try:
            service = LeadService(session, request.principal)
            data = service.detail(lead_id)
            service.record_view(lead_id)
            session.commit()
            response = jsonify(data)
            response.headers["Cache-Control"] = "no-store"
            return response
        except RecordNotFound as exc:
            return jsonify({"error": str(exc)}), 404


@leads_bp.route("/<int:lead_id>", methods=["PUT"])
@requires_auth()
async def update_lead(lead_id):
    raw_data = await request.get_json()
    if not isinstance(raw_data, dict):
        return jsonify({"error": "Invalid request body"}), 400
    try:
        data = LeadUpdateSchema(**raw_data)
    except ValidationError as exc:
        return jsonify({"error": "Validation failed", "details": exc.errors()}), 400
    with SessionLocal() as session:
        try:
            result = LeadService(session, request.principal).update(lead_id, data)
            session.commit()
            return jsonify({"id": result})
        except RecordNotFound as exc:
            return jsonify({"error": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400


@leads_bp.route("/<int:lead_id>", methods=["DELETE"])
@requires_auth()
async def delete_lead(lead_id):
    with SessionLocal() as session:
        try:
            changed = LeadService(session, request.principal).delete(lead_id)
            session.commit()
            return jsonify({"message": "Lead soft-deleted successfully" if changed else "Lead already deleted"})
        except RecordNotFound as exc:
            return jsonify({"error": str(exc)}), 404


@leads_bp.route("/<int:lead_id>/assign", methods=["PUT"])
@requires_auth(roles=["admin"])
async def assign_lead(lead_id):
    raw_data = await request.get_json()
    if not isinstance(raw_data, dict):
        return jsonify({"error": "Invalid request body"}), 400
    try:
        data = LeadAssignSchema(**raw_data)
    except ValidationError as exc:
        return jsonify({"error": "Validation failed", "details": exc.errors()}), 400
    with SessionLocal() as session:
        try:
            notification = LeadService(session, request.principal).assign(lead_id, data)
            session.commit()
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 403
        except RecordNotFound as exc:
            return jsonify({"error": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
    # Notification delivery is best effort and cannot undo a committed assignment.
    try:
        await send_assignment_notification(**notification, assigned_by=request.user.email)
    except Exception:
        pass
    return jsonify({"message": "Lead assigned successfully"})


@leads_bp.route("/all", methods=["GET"])
@requires_auth(roles=["admin"])
async def list_all_leads_admin():
    with SessionLocal() as session:
        try:
            result = LeadService(session, request.principal).list_all(page=int(request.args.get("page", 1)), per_page=int(request.args.get("per_page", 20)), sort_order=request.args.get("sort", "newest"), user_email=request.args.get("user_email"))
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store"
            return response
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 403
        except RecordNotFound as exc:
            return jsonify({"error": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400


@leads_bp.route("/assigned", methods=["GET"])
@requires_auth(roles=["admin"])
async def list_assigned_leads():
    with SessionLocal() as session:
        try:
            result = LeadService(session, request.principal).list_assigned()
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store"
            return response
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 403
        except RecordNotFound as exc:
            return jsonify({"error": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400


@leads_bp.route("/bulk-delete", methods=["POST"])
@requires_auth(roles=["admin"])
async def bulk_delete_leads():
    data = await request.get_json()
    with SessionLocal() as session:
        try:
            result = LeadService(session, request.principal).bulk_delete(data.get("lead_ids") if isinstance(data, dict) else None)
            session.commit()
            return jsonify({"message": f"{result} lead(s) deleted"})
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 403
        except RecordNotFound as exc:
            return jsonify({"error": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400


@leads_bp.route("/bulk-purge", methods=["DELETE", "POST"])
@requires_auth(roles=["admin"])
async def bulk_purge_leads():
    data = await request.get_json()
    with SessionLocal() as session:
        try:
            result = LeadService(session, request.principal).bulk_purge(data.get("lead_ids") if isinstance(data, dict) else None)
            session.commit()
            return jsonify({"message": f"{result} lead(s) permanently deleted"})
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 403
        except RecordNotFound as exc:
            return jsonify({"error": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400


@leads_bp.route("/trash", methods=["GET"])
@requires_auth()
async def list_trashed_leads():
    with SessionLocal() as session:
        try:
            result = LeadService(session, request.principal).list_trash()
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store"
            return response
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 403
        except RecordNotFound as exc:
            return jsonify({"error": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400


@leads_bp.route("/<int:lead_id>/restore", methods=["PUT"])
@requires_auth()
async def restore_lead(lead_id):
    with SessionLocal() as session:
        try:
            LeadService(session, request.principal).restore(lead_id)
            session.commit()
            return jsonify({"message": "Lead restored successfully"})
        except RecordNotFound:
            return jsonify({"error": "Lead not found or not authorized to restore"}), 404


@leads_bp.route("/<int:lead_id>/purge", methods=["DELETE"])
@requires_auth(roles=["admin"])
async def purge_lead(lead_id):
    with SessionLocal() as session:
        try:
            result = LeadService(session, request.principal).purge(lead_id)
            session.commit()
            return jsonify({"message": "Lead permanently deleted"}), 200
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 403
        except RecordNotFound as exc:
            return jsonify({"error": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

