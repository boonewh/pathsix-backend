from quart import Blueprint, request, jsonify
from pydantic import ValidationError
from app.database import SessionLocal
from app.utils.auth_utils import requires_auth
from app.schemas.subscriptions import SubscriptionCreateSchema, SubscriptionUpdateSchema
from app.services.subscriptions import SubscriptionService
from app.services.errors import RecordNotFound

subscriptions_bp = Blueprint("subscriptions", __name__, url_prefix="/api/subscriptions")


async def _operation(method, sub_id=None, schema=None):
    data = None
    if schema:
        raw = await request.get_json()
        if not isinstance(raw, dict):
            return jsonify({"error": "Invalid request body"}), 400
        try:
            data = schema(**raw)
        except ValidationError:
            return jsonify({"error": "Invalid subscription data"}), 400
    with SessionLocal() as session:
        try:
            service = SubscriptionService(session, request.principal)
            if method == "list_visible":
                result = service.list_visible(
                    client_id=request.args.get("client_id"),
                    status=request.args.get("status"),
                )
            else:
                args = ([] if sub_id is None else [sub_id]) + (
                    [] if data is None else [data]
                )
                result = getattr(service, method)(*args)
            if method not in ("list_visible", "detail"):
                session.commit()
            response = jsonify(
                result if method != "delete" else {"message": "Subscription deleted"}
            )
            if method in ("list_visible", "detail"):
                response.headers["Cache-Control"] = "no-store"
            return response, 201 if method == "create" else 200
        except RecordNotFound as exc:
            return jsonify({"error": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400


@subscriptions_bp.route("", methods=["GET"])
@subscriptions_bp.route("/", methods=["GET"])
@requires_auth()
async def list_subscriptions():
    return await _operation("list_visible")


@subscriptions_bp.route("", methods=["POST"])
@subscriptions_bp.route("/", methods=["POST"])
@requires_auth()
async def create_subscription():
    return await _operation("create", schema=SubscriptionCreateSchema)


@subscriptions_bp.route("/<int:sub_id>", methods=["GET"])
@requires_auth()
async def get_subscription(sub_id):
    return await _operation("detail", sub_id)


@subscriptions_bp.route("/<int:sub_id>", methods=["PUT"])
@requires_auth()
async def update_subscription(sub_id):
    return await _operation("update", sub_id, SubscriptionUpdateSchema)


@subscriptions_bp.route("/<int:sub_id>", methods=["DELETE"])
@requires_auth()
async def delete_subscription(sub_id):
    return await _operation("delete", sub_id)


@subscriptions_bp.route("/<int:sub_id>/renew", methods=["POST"])
@requires_auth()
async def renew_subscription(sub_id):
    return await _operation("renew", sub_id)
