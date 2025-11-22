from quart import Blueprint, request, jsonify, current_app
from sqlalchemy.exc import SQLAlchemyError
from app.models import User
from app.database import SessionLocal
from app.utils.auth_utils import (
    verify_password,
    create_token,
    hash_password,
    generate_reset_token,
    verify_reset_token
)
from app.utils.auth_utils import requires_auth
from app.utils.email_utils import send_email


auth_bp = Blueprint("auth", __name__, url_prefix="/api")

@auth_bp.route("/login", methods=["POST"])
async def login():
    data = await request.get_json()

    email = data.get("email", "").lower().strip()
    password = data.get("password")

    if not email or not password:
        return jsonify({"error": "Missing credentials"}), 400

    session = SessionLocal()
    try:
        user = session.query(User).filter_by(email=email).first()
        if not user or not verify_password(password, user.password_hash):
            return jsonify({"error": "Invalid credentials"}), 401

        token = create_token(user)

        response = jsonify({
            "user": {
                "id": user.id,
                "email": user.email,
                "roles": [role.name for role in user.roles]
            },
            "token": token
        })
        response.headers["Cache-Control"] = "no-store"
        return response
    except SQLAlchemyError as e:
        session.rollback()
        return jsonify({"error": "Server error"}), 500
    finally:
        session.close()

@auth_bp.route("/forgot-password", methods=["POST"])
async def forgot_password():
    data = await request.get_json()
    email = data.get("email", "").lower().strip()
    if not email:
        return jsonify({"error": "Missing email"}), 400

    session = SessionLocal()
    try:
        user = session.query(User).filter_by(email=email).first()
        if not user:
            return jsonify({"message": "If that account exists, an email was sent."})  # Don't reveal info

        token = generate_reset_token(email)
        reset_link = f"{current_app.config['FRONTEND_URL']}/reset-password/{token}"

        await send_email(
            subject="Password Reset Request",
            recipient=email,
            body=f"Click to reset your password: {reset_link}"
        )
        print("Reset link:", reset_link)

        return jsonify({"message": "If that account exists, a reset email was sent."})
    except SQLAlchemyError:
        session.rollback()
        return jsonify({"error": "Server error"}), 500
    finally:
        session.close()

@auth_bp.route("/reset-password", methods=["POST"])
async def reset_password():
    data = await request.get_json()
    token = data.get("token")
    new_password = data.get("password")

    if not token or not new_password:
        return jsonify({"error": "Missing token or password"}), 400

    email = verify_reset_token(token)
    if not email:
        return jsonify({"error": "Invalid or expired token"}), 400

    session = SessionLocal()
    try:
        user = session.query(User).filter_by(email=email).first()
        if not user:
            return jsonify({"error": "User not found"}), 404

        user.password_hash = hash_password(new_password)
        session.commit()

        return jsonify({"message": "Password updated successfully"})
    except SQLAlchemyError:
        session.rollback()
        return jsonify({"error": "Server error"}), 500
    finally:
        session.close()

@auth_bp.route("/change-password", methods=["POST"])
@requires_auth()
async def change_password():
    data = await request.get_json()
    current_password = data.get("current_password")
    new_password = data.get("new_password")
    user = request.user

    if not current_password or not new_password:
        return jsonify({"error": "Missing required fields"}), 400

    if not verify_password(current_password, user.password_hash):
        return jsonify({"error": "Incorrect current password"}), 403

    session = SessionLocal()
    try:
        user = session.get(User, user.id)
        user.password_hash = hash_password(new_password)
        session.commit()
        return jsonify({"message": "Password changed successfully"})
    except SQLAlchemyError:
        session.rollback()
        return jsonify({"error": "Server error"}), 500
    finally:
        session.close()


@auth_bp.route("/me", methods=["GET"])
@requires_auth()
async def get_me():
    user = request.user
    return jsonify({
        "id": user.id,
        "email": user.email,
        "roles": [r.name for r in user.roles]
    })
