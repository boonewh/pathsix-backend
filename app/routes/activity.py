from quart import Blueprint, jsonify, request
from app.database import SessionLocal
from app.utils.auth_utils import requires_auth
from app.services.activity import ActivityService

activity_bp = Blueprint("activity", __name__, url_prefix="/api/activity")


@activity_bp.route("/recent", methods=["GET"])
@requires_auth()
async def recent_activity():
    try:
        limit = int(request.args.get("limit", "10"))
        with SessionLocal() as session:
            result = ActivityService(session, request.principal).recent(limit)
        response = jsonify(result)
        response.headers["Cache-Control"] = "no-store"
        return response
    except ValueError:
        return jsonify({"error": "Limit must be a positive integer"}), 400
