from quart import Blueprint, request, jsonify, current_app
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload
from app.models import User
from app.database import SessionLocal
from app.services.database_context import auth_lookup
from app.services.identity import IdentityService
from app.utils.auth_utils import (
    verify_password,
    create_token,
    hash_password,
    verify_reset_token
)
from app.utils.auth_utils import requires_auth
from app.utils.email_utils import send_password_reset_email
from app.utils.rate_limiter import rate_limit


auth_bp = Blueprint("auth", __name__, url_prefix="/api")

@auth_bp.route("/login", methods=["POST"])
@rate_limit(max_attempts=5, window_seconds=60)  # 5 login attempts per minute per IP
async def login():
    data = await request.get_json()

    email = data.get("email", "").lower().strip()
    password = data.get("password")

    if not email or not password:
        return jsonify({"error": "Missing credentials"}), 400

    session = SessionLocal()
    try:
        auth_lookup(session, email=email)
        # Load user with tenant relationship for config
        user = session.query(User)\
            .options(joinedload(User.tenant), joinedload(User.roles))\
            .filter_by(email=email)\
            .first()
        if not user or not user.is_active or not user.tenant or not user.tenant.is_active or not verify_password(password, user.password_hash):
            return jsonify({"error": "Invalid credentials"}), 401

        token = create_token(user)

        # Build response with tenant config
        response_data = {
            "user": {
                "id": user.id,
                "email": user.email,
                "roles": [role.name for role in user.roles],
                "tenant_id": user.tenant_id,
            },
            "token": token
        }

        # Include tenant config if available
        if user.tenant:
            response_data["tenant"] = {
                "id": user.tenant.id,
                "name": user.tenant.name,
                "slug": user.tenant.slug,
                "config": user.tenant.config
            }

        response = jsonify(response_data)
        response.headers["Cache-Control"] = "no-store"
        return response
    except SQLAlchemyError as e:
        session.rollback()
        import logging
        logging.error(f"Login error: {e}")
        return jsonify({"error": "Server error"}), 500
    except Exception as e:
        session.rollback()
        import logging
        logging.error(f"Login unexpected error: {e}")
        return jsonify({"error": "Server error"}), 500
    finally:
        session.close()

@auth_bp.route("/forgot-password", methods=["POST"])
@rate_limit(max_attempts=6, window_seconds=300)  # 6 password reset attempts per 5 minutes per IP
async def forgot_password():
    data = await request.get_json()
    email = data.get("email", "").lower().strip()
    if not email:
        return jsonify({"error": "Missing email"}), 400

    session = SessionLocal()
    try:
        auth_lookup(session, email=email)
        user = session.query(User).filter_by(email=email).first()
        if not user:
            return jsonify({"message": "If that account exists, an email was sent."})

        try:
            await send_password_reset_email(email)
        except Exception:
            current_app.logger.exception("Password reset email delivery failed")
            return jsonify({
                "error": "Unable to send a reset email right now. Please try again later."
            }), 503

        return jsonify({"message": "If that account exists, a reset email was sent."})
    except SQLAlchemyError:
        session.rollback()
        return jsonify({"error": "Server error"}), 500
    finally:
        session.close()

@auth_bp.route("/reset-password", methods=["POST"])
@rate_limit(max_attempts=10, window_seconds=300)
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
        # Only reached after the signed reset token has been verified.
        auth_lookup(session, email=email, password_reset=True)
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

def _identity_response(operation, *, write=False):
    with SessionLocal() as session:
        try:
            if write:
                request.database_write_started = True
            result = operation(IdentityService(session, request.principal))
            if write:
                session.commit()
            response = jsonify(result)
            response.headers['Cache-Control'] = 'no-store'
            return response
        except PermissionError as exc:
            session.rollback()
            return jsonify({'error': str(exc)}), 403
        except ValueError as exc:
            session.rollback()
            return jsonify({'error': str(exc)}), 400
        except SQLAlchemyError:
            session.rollback()
            raise


@auth_bp.route('/change-password', methods=['POST'])
@requires_auth()
async def change_password():
    data = await request.get_json()
    return _identity_response(lambda service: service.change_password(data), write=True)


@auth_bp.route('/me', methods=['GET'])
@requires_auth()
async def get_me():
    return _identity_response(lambda service: service.me())


@auth_bp.route('/tenant/config', methods=['GET'])
@requires_auth()
async def get_tenant_config():
    return _identity_response(lambda service: service.tenant_config())
