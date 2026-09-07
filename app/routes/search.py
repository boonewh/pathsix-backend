from quart import Blueprint, request, jsonify
from app.database import SessionLocal
from app.services.search import SearchService
from app.utils.auth_utils import requires_auth

search_bp = Blueprint("search", __name__, url_prefix="/api/search")


@search_bp.route("", methods=["GET"])
@search_bp.route("/", methods=["GET"])
@requires_auth()
async def global_search():
    with SessionLocal() as session:
        try:
            results = SearchService(session, request.principal).search(request.args.get("q", ""))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(results)
