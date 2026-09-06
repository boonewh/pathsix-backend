import bcrypt
import logging
import sentry_sdk
import time
from authlib.jose import jwt, JoseError, JsonWebToken
from quart import request, jsonify, current_app
from functools import wraps
from app.models import User
from app.database import SessionLocal
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.orm import joinedload

logger = logging.getLogger(__name__)


def _rollback_quietly(session):
    try:
        session.rollback()
    except SQLAlchemyError:
        pass

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))

def create_token(user: User) -> str:
    header = {"alg": "HS256"}
    payload = {
        "sub": user.id,
        "email": user.email,
        "roles": [r.name for r in user.roles],
        "exp": int(time.time()) + 30 * 86400  # 30 days
    }
    return jwt.encode(header, payload, current_app.config["SECRET_KEY"]).decode("utf-8")

def decode_token(token: str):
    claims = JsonWebToken(["HS256"]).decode(token, current_app.config["SECRET_KEY"],
        claims_options={"sub": {"essential": True}, "exp": {"essential": True}})
    claims.validate()
    if not isinstance(claims["sub"], (int, str)) or not str(claims["sub"]).isdigit():
        raise JoseError("Invalid subject")
    return claims

def requires_auth(roles: list = None):
    def wrapper(fn):
        @wraps(fn)
        async def decorated(*args, **kwargs):
            if request.method == "OPTIONS":
                return "", 204
            
            auth_header = request.headers.get("Authorization", None)
            if not auth_header or not auth_header.startswith("Bearer "):
                return jsonify({"error": "Missing or invalid token"}), 401
            token = auth_header.split(" ")[1]
            try:
                payload = decode_token(token)
            except (JoseError, ValueError, TypeError):
                return jsonify({"error": "Invalid token"}), 401

            # SQLAlchemy cannot transparently recover when PostgreSQL drops a
            # connection mid-query. Retrying the whole transaction is safe for
            # read-only requests, but not for writes that may already have committed.
            max_attempts = 2 if request.method in {"GET", "HEAD"} else 1

            for attempt in range(max_attempts):
                session = SessionLocal()
                try:
                    user = session.query(User)\
                        .options(joinedload(User.roles), joinedload(User.tenant))\
                        .filter(User.id == payload["sub"], User.is_active == True)\
                        .first()

                    if not user:
                        return jsonify({"error": "User not found"}), 401
                    if not user.tenant or not user.tenant.is_active:
                        return jsonify({"error": "Tenant is inactive"}), 403
                    if roles and not any(role.name in roles for role in user.roles):
                        return jsonify({"error": "Forbidden"}), 403

                    request.user = user
                    return await fn(*args, **kwargs)
                except DBAPIError as exc:
                    _rollback_quietly(session)
                    if (exc.connection_invalidated and attempt + 1 < max_attempts
                            and not getattr(request, "database_write_started", False)):
                        logger.warning(
                            "Retrying read-only request after database disconnect",
                            extra={"path": request.path, "attempt": attempt + 1},
                        )
                        continue

                    sentry_sdk.capture_exception(exc)
                    return jsonify({"error": "Database error"}), 500
                except SQLAlchemyError as exc:
                    _rollback_quietly(session)
                    sentry_sdk.capture_exception(exc)
                    return jsonify({"error": "Database error"}), 500
                finally:
                    session.close()
        return decorated
    return wrapper

def generate_reset_token(email: str) -> str:
    s = URLSafeTimedSerializer(current_app.config["SECRET_KEY"])
    return s.dumps(email, salt="password-reset")

def verify_reset_token(token: str, max_age=1800) -> str | None:
    s = URLSafeTimedSerializer(current_app.config["SECRET_KEY"])
    try:
        email = s.loads(token, salt="password-reset", max_age=max_age)
        return email
    except Exception:
        return None
