from quart import Blueprint, request, jsonify
from pydantic import ValidationError
from app.database import SessionLocal
from app.utils.auth_utils import requires_auth
from app.schemas.accounts import AccountCreateSchema, AccountUpdateSchema
from app.services.accounts import AccountService
from app.services.errors import RecordNotFound

accounts_bp = Blueprint("accounts", __name__, url_prefix="/api/accounts")


async def _operation(method, account_id=None, schema=None):
    data = None
    if schema:
        raw = await request.get_json()
        if not isinstance(raw, dict):
            return jsonify({"error": "Invalid request body"}), 400
        try:
            data = schema(**raw)
        except ValidationError:
            return jsonify({"error": "Invalid account data"}), 400
    with SessionLocal() as session:
        try:
            service = AccountService(session, request.principal)
            args = ([] if account_id is None else [account_id]) + (
                [] if data is None else [data]
            )
            result = getattr(service, method)(*args)
            if method == "detail":
                service.record_view(account_id)
            if method != "list_visible":
                session.commit()
            response = jsonify(
                result if method != "delete" else {"message": "Account deleted"}
            )
            if method in ("list_visible", "detail"):
                response.headers["Cache-Control"] = "no-store"
            return response, 201 if method == "create" else 200
        except RecordNotFound as exc:
            return jsonify({"error": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400


@accounts_bp.route("", methods=["GET"])
@accounts_bp.route("/", methods=["GET"])
@requires_auth()
async def list_accounts():
    return await _operation("list_visible")


@accounts_bp.route("", methods=["POST"])
@accounts_bp.route("/", methods=["POST"])
@requires_auth()
async def create_account():
    return await _operation("create", schema=AccountCreateSchema)


@accounts_bp.route("/<int:account_id>", methods=["GET"])
@requires_auth()
async def get_account(account_id):
    return await _operation("detail", account_id)


@accounts_bp.route("/<int:account_id>", methods=["PUT"])
@requires_auth()
async def update_account(account_id):
    return await _operation("update", account_id, AccountUpdateSchema)


@accounts_bp.route("/<int:account_id>", methods=["DELETE"])
@requires_auth()
async def delete_account(account_id):
    return await _operation("delete", account_id)
