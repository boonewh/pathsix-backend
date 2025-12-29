from quart import Blueprint, request, jsonify
from datetime import datetime
from app.models import Account, ActivityLog, ActivityType
from app.database import SessionLocal
from app.utils.auth_utils import requires_auth
from app.constants import ACCOUNT_STATUS_OPTIONS
from sqlalchemy.orm import joinedload

accounts_bp = Blueprint("accounts", __name__, url_prefix="/api/accounts")


@accounts_bp.route("", methods=["GET"])
@accounts_bp.route("", methods=["GET"])
@accounts_bp.route("/", methods=["GET"])
@requires_auth()
async def list_accounts():
    user = request.user
    session = SessionLocal()
    try:
        accounts = session.query(Account).options(
            joinedload(Account.client)
        ).filter(
            Account.tenant_id == user.tenant_id
        ).all()

        response = jsonify([
            {
                "id": a.id,
                "client_id": a.client_id,
                "client_name": a.client.name if a.client else None,
                "account_number": a.account_number,
                "account_name": a.account_name,
                "status": a.status,
                "opened_on": a.opened_on.isoformat() + "Z" if a.opened_on else None,
                "notes": a.notes
            } for a in accounts
        ])
        response.headers["Cache-Control"] = "no-store"
        return response
    finally:
        session.close()


@accounts_bp.route("", methods=["POST"])
@accounts_bp.route("", methods=["POST"])
@accounts_bp.route("/", methods=["POST"])
@requires_auth()
async def create_account():
    user = request.user
    data = await request.get_json()
    session = SessionLocal()
    try:
        if not data.get("client_id") or not data.get("account_number"):
            return jsonify({"error": "client_id and account_number are required"}), 400

        status = data.get("status", ACCOUNT_STATUS_OPTIONS[0])
        if status not in ACCOUNT_STATUS_OPTIONS:
            status = ACCOUNT_STATUS_OPTIONS[0]

        account = Account(
            tenant_id=user.tenant_id,
            client_id=data["client_id"],
            account_number=data["account_number"],
            account_name=data.get("account_name"),
            status=status,
            opened_on=datetime.fromisoformat(data["opened_on"]) if data.get("opened_on") else datetime.utcnow(),
            notes=data.get("notes")
        )
        session.add(account)
        session.commit()
        session.refresh(account)
        return jsonify({
            "id": account.id,
            "account_number": account.account_number,
            "status": account.status,
            "client_id": account.client_id,
            "account_name": account.account_name,
            "opened_on": account.opened_on.isoformat() + "Z",
            "notes": account.notes
        }), 201
    finally:
        session.close()


@accounts_bp.route("/<int:account_id>", methods=["PUT"])
@requires_auth()
async def update_account(account_id):
    user = request.user
    data = await request.get_json()
    session = SessionLocal()
    try:
        account = session.query(Account).filter(
            Account.id == account_id,
            Account.tenant_id == user.tenant_id
        ).first()

        if not account:
            return jsonify({"error": "Account not found"}), 404

        for field in ["account_number", "account_name", "notes", "client_id"]:
            if field in data:
                setattr(account, field, data[field])

        if "status" in data and data["status"] in ACCOUNT_STATUS_OPTIONS:
            account.status = data["status"]

        if "opened_on" in data and data["opened_on"]:
            try:
                account.opened_on = datetime.fromisoformat(data["opened_on"])
            except ValueError:
                return jsonify({"error": "Invalid opened_on format"}), 400

        session.commit()
        session.refresh(account)
        return jsonify({
            "id": account.id,
            "account_number": account.account_number,
            "status": account.status,
            "client_id": account.client_id,
            "account_name": account.account_name,
            "opened_on": account.opened_on.isoformat() + "Z" if account.opened_on else None,
            "notes": account.notes
        })
    finally:
        session.close()


@accounts_bp.route("/<int:account_id>", methods=["DELETE"])
@requires_auth()
async def delete_account(account_id):
    user = request.user
    session = SessionLocal()
    try:
        account = session.query(Account).filter(
            Account.id == account_id,
            Account.tenant_id == user.tenant_id
        ).first()

        if not account:
            return jsonify({"error": "Account not found"}), 404

        session.delete(account)
        session.commit()
        return jsonify({"message": "Account deleted"})
    finally:
        session.close()

@accounts_bp.route("/<int:account_id>", methods=["GET"])
@requires_auth()
async def get_account(account_id):
    user = request.user
    session = SessionLocal()
    try:
        account = session.query(Account).options(
            joinedload(Account.client)
        ).filter(
            Account.id == account_id,
            Account.tenant_id == user.tenant_id
        ).first()

        if not account:
            return jsonify({"error": "Account not found"}), 404

        log = ActivityLog(
            tenant_id=user.tenant_id,
            user_id=user.id,
            action=ActivityType.viewed,
            entity_type="account",
            entity_id=account.id,
            description=f"Viewed account '{account.account_number}'"
        )
        session.add(log)
        session.commit()

        response = jsonify({
            "id": account.id,
            "client_id": account.client_id,
            "client_name": account.client.name if account.client else None,
            "account_name": account.account_name,
            "account_number": account.account_number,
            "status": account.status,
            "notes": account.notes,
            "opened_on": account.opened_on.isoformat() + "Z" if account.opened_on else None
        })
        response.headers["Cache-Control"] = "no-store"
        return response
    finally:
        session.close()

