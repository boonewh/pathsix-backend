import bcrypt
import time
from authlib.jose import jwt, JoseError
from quart import request, jsonify, current_app
from functools import wraps
from app.models import User
from app.database import SessionLocal
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload

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
    return jwt.decode(token, current_app.config["SECRET_KEY"])

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
            except JoseError:
                return jsonify({"error": "Invalid token"}), 401

            session = SessionLocal()
            try:
                user = session.query(User)\
                    .options(joinedload(User.roles))\
                    .filter(User.id == payload["sub"], User.is_active == True)\
                    .first()
            except SQLAlchemyError:
                session.rollback()
                return jsonify({"error": "Database error"}), 500
            finally:
                session.close()

            if not user:
                return jsonify({"error": "User not found"}), 401
            if roles and not any(role in payload["roles"] for role in roles):
                return jsonify({"error": "Forbidden"}), 403

            request.user = user
            return await fn(*args, **kwargs)
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
