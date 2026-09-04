import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from quart import Quart

from app.routes import auth as auth_routes
from app.routes import users as user_routes
from app.utils import auth_utils, email_utils
from app.utils.rate_limiter import reset_rate_limit


class _Query:
    def __init__(self, result):
        self.result = result

    def options(self, *args):
        return self

    def filter(self, *args):
        return self

    def filter_by(self, **kwargs):
        return self

    def first(self):
        return self.result


class _Session:
    def __init__(self, result):
        self.result = result
        self.closed = False

    def query(self, model):
        return _Query(self.result)

    def rollback(self):
        pass

    def close(self):
        self.closed = True


def test_password_reset_email_uses_configured_frontend(monkeypatch):
    send_email = AsyncMock()
    monkeypatch.setattr(email_utils, "generate_reset_token", lambda email: "reset-token")
    monkeypatch.setattr(email_utils, "send_email", send_email)

    app = Quart(__name__)
    app.config["FRONTEND_URL"] = "https://crm.example.test"

    async def exercise():
        async with app.app_context():
            await email_utils.send_password_reset_email("user@example.test")

    asyncio.run(exercise())

    send_email.assert_awaited_once_with(
        subject="Password Reset Request",
        recipient="user@example.test",
        body=(
            "Click to reset your password: "
            "https://crm.example.test/reset-password/reset-token"
        ),
    )


def test_forgot_password_reports_delivery_failure(monkeypatch):
    user = SimpleNamespace(email="user@example.test")
    session = _Session(user)
    send_password_reset_email = AsyncMock(side_effect=RuntimeError("SMTP unavailable"))

    monkeypatch.setattr(auth_routes, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        auth_routes, "send_password_reset_email", send_password_reset_email
    )
    reset_rate_limit()

    app = Quart(__name__)
    app.register_blueprint(auth_routes.auth_bp)

    async def exercise():
        client = app.test_client()
        response = await client.post(
            "/api/forgot-password", json={"email": "user@example.test"}
        )
        assert response.status_code == 503
        assert await response.get_json() == {
            "error": "Unable to send a reset email right now. Please try again later."
        }

    asyncio.run(exercise())

    send_password_reset_email.assert_awaited_once_with("user@example.test")
    assert session.closed


def test_forgot_password_allows_six_requests_per_five_minutes(monkeypatch):
    monkeypatch.setattr(auth_routes, "SessionLocal", lambda: _Session(None))
    reset_rate_limit()

    app = Quart(__name__)
    app.register_blueprint(auth_routes.auth_bp)

    async def exercise():
        client = app.test_client()
        for _ in range(6):
            response = await client.post(
                "/api/forgot-password", json={"email": "missing@example.test"}
            )
            assert response.status_code == 200

        limited = await client.post(
            "/api/forgot-password", json={"email": "missing@example.test"}
        )
        assert limited.status_code == 429

    asyncio.run(exercise())


def test_admin_can_send_password_reset_within_tenant(monkeypatch):
    admin = SimpleNamespace(id=1, tenant_id=7, roles=[])
    target = SimpleNamespace(id=2, tenant_id=7, email="user@example.test")
    auth_session = _Session(admin)
    target_session = _Session(target)
    send_password_reset_email = AsyncMock()

    monkeypatch.setattr(auth_utils, "SessionLocal", lambda: auth_session)
    monkeypatch.setattr(
        auth_utils,
        "decode_token",
        lambda token: {"sub": 1, "roles": ["admin"]},
    )
    monkeypatch.setattr(user_routes, "SessionLocal", lambda: target_session)
    monkeypatch.setattr(
        user_routes, "send_password_reset_email", send_password_reset_email
    )

    app = Quart(__name__)
    app.register_blueprint(user_routes.users_bp)

    async def exercise():
        client = app.test_client()
        response = await client.post(
            "/api/users/2/send-password-reset",
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200
        assert await response.get_json() == {"message": "Password reset email sent"}

    asyncio.run(exercise())

    send_password_reset_email.assert_awaited_once_with("user@example.test")
    assert auth_session.closed
    assert target_session.closed


def test_admin_password_reset_reports_delivery_failure(monkeypatch):
    admin = SimpleNamespace(id=1, tenant_id=7, roles=[])
    target = SimpleNamespace(id=2, tenant_id=7, email="user@example.test")
    send_password_reset_email = AsyncMock(side_effect=RuntimeError("SMTP unavailable"))

    monkeypatch.setattr(auth_utils, "SessionLocal", lambda: _Session(admin))
    monkeypatch.setattr(
        auth_utils,
        "decode_token",
        lambda token: {"sub": 1, "roles": ["admin"]},
    )
    monkeypatch.setattr(user_routes, "SessionLocal", lambda: _Session(target))
    monkeypatch.setattr(
        user_routes, "send_password_reset_email", send_password_reset_email
    )

    app = Quart(__name__)
    app.register_blueprint(user_routes.users_bp)

    async def exercise():
        client = app.test_client()
        response = await client.post(
            "/api/users/2/send-password-reset",
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 503
        assert await response.get_json() == {
            "error": (
                "Unable to send the password reset email right now. "
                "Please try again later."
            )
        }

    asyncio.run(exercise())
