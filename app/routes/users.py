from quart import Blueprint, request, jsonify
from app.models import User, Role, ActivityLog, ActivityType
from app.database import SessionLocal
from app.utils.auth_utils import requires_auth, hash_password

users_bp = Blueprint("users", __name__, url_prefix="/api/users")

from sqlalchemy.orm import joinedload

@users_bp.route("", methods=["GET"])
@users_bp.route("/", methods=["GET"])
@requires_auth(roles=["admin"])
async def list_users():
    user = request.user
    session = SessionLocal()
    try:
        users = session.query(User).options(joinedload(User.roles)).filter(
            User.tenant_id == user.tenant_id
        ).all()

        response = jsonify([
            {
                "id": u.id,
                "email": u.email,
                "created_at": u.created_at.isoformat() + "Z",
                "roles": [r.name for r in u.roles],
                "is_active": u.is_active
            }
            for u in users
        ])
        response.headers["Cache-Control"] = "no-store"
        return response
    finally:
        session.close()


@users_bp.route("", methods=["POST"])
@users_bp.route("/", methods=["POST"])
@requires_auth(roles=["admin"])
async def create_user():
    user = request.user
    data = await request.get_json()
    session = SessionLocal()
    try:
        email = data.get("email")
        password = data.get("password")
        role_names = data.get("roles", [])

        if not email or not password:
            return jsonify({"error": "Email and password are required"}), 400

        if session.query(User).filter_by(email=email).first():
            return jsonify({"error": "User already exists"}), 400

        roles = session.query(Role).filter(Role.name.in_(role_names)).all()

        if not roles and role_names:
            return jsonify({"error": "One or more roles not found"}), 400

        new_user = User(
            tenant_id=user.tenant_id,
            email=email,
            password_hash=hash_password(password),
            is_active=True
        )

        new_user.roles.extend(roles)

        session.add(new_user)
        session.commit()
        session.refresh(new_user)

        return jsonify({
            "id": new_user.id,
            "email": new_user.email,
            "roles": [r.name for r in new_user.roles],
            "is_active": new_user.is_active
        }), 201

    finally:
        session.close()

@users_bp.route("/<int:user_id>/toggle-active", methods=["PUT"])
@requires_auth(roles=["admin"])
async def toggle_user_active(user_id):
    user = request.user
    session = SessionLocal()
    try:
        target = session.query(User).filter(
            User.id == user_id,
            User.tenant_id == user.tenant_id
        ).first()

        if not target:
            return jsonify({"error": "User not found"}), 404

        if user.id == target.id:
            return jsonify({"error": "You cannot deactivate yourself"}), 403

        target.is_active = not target.is_active
        session.commit()

        return jsonify({
            "id": target.id,
            "is_active": target.is_active
        })
    finally:
        session.close()

@users_bp.route("/<int:user_id>/roles", methods=["PUT"])
@requires_auth(roles=["admin"])
async def update_user_roles(user_id):
    user = request.user
    data = await request.get_json()
    new_roles = data.get("roles", [])

    session = SessionLocal()
    try:
        target = session.query(User).filter(
            User.id == user_id,
            User.tenant_id == user.tenant_id
        ).first()

        if not target:
            return jsonify({"error": "User not found"}), 404

        roles = session.query(Role).filter(Role.name.in_(new_roles)).all()

        if not roles and new_roles:
            return jsonify({"error": "One or more roles not found"}), 400

        target.roles = roles
        session.commit()

        return jsonify({
            "id": target.id,
            "roles": [r.name for r in target.roles]
        })
    finally:
        session.close()

@users_bp.route("/<int:user_id>", methods=["PUT"])
@requires_auth(roles=["admin"])
async def update_user_email(user_id):
    user = request.user
    data = await request.get_json()
    session = SessionLocal()
    try:
        target = session.query(User).filter(
            User.id == user_id,
            User.tenant_id == user.tenant_id
        ).first()

        if not target:
            return jsonify({"error": "User not found"}), 404

        new_email = data.get("email")
        if not new_email:
            return jsonify({"error": "Email is required"}), 400

        if session.query(User).filter(
            User.email == new_email,
            User.id != user_id
        ).first():
            return jsonify({"error": "Another user already has that email"}), 400

        target.email = new_email
        session.commit()

        return jsonify({
            "id": target.id,
            "email": target.email,
            "roles": [r.name for r in target.roles],
            "created_at": target.created_at.isoformat() + "Z",
            "is_active": target.is_active
        })

    finally:
        session.close()
