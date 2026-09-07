import asyncio
from types import SimpleNamespace

from quart import Quart, jsonify
from sqlalchemy.exc import OperationalError

from app.utils import auth_utils


class _Query:
    def __init__(self, user):
        self.user = user

    def options(self, *args):
        return self

    def filter(self, *args):
        return self

    def first(self):
        return self.user


class _Session:
    def __init__(self, user):
        self.user = user
        self.closed = False

    def query(self, model):
        return _Query(self.user)

    def rollback(self):
        pass

    def close(self):
        self.closed = True


def test_read_only_request_retries_once_after_disconnect(monkeypatch):
    user = SimpleNamespace(id=1, tenant_id=1, is_active=True, tenant=SimpleNamespace(is_active=True), roles=[])
    sessions = []
    handler_calls = 0

    def make_session():
        session = _Session(user)
        sessions.append(session)
        return session

    monkeypatch.setattr(auth_utils, "SessionLocal", make_session)
    monkeypatch.setattr(
        auth_utils,
        "decode_token",
        lambda token: {"sub": 1, "roles": []},
    )

    app = Quart(__name__)

    @app.get("/retry")
    @auth_utils.requires_auth()
    async def retry_route():
        nonlocal handler_calls
        handler_calls += 1
        if handler_calls == 1:
            raise OperationalError(
                "SELECT 1",
                {},
                Exception("server closed the connection unexpectedly"),
                connection_invalidated=True,
            )
        return jsonify({"status": "ok"})

    async def exercise_route():
        client = app.test_client()
        response = await client.get(
            "/retry", headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 200
        assert await response.get_json() == {"status": "ok"}

    asyncio.run(exercise_route())

    assert handler_calls == 2
    assert len(sessions) == 2
    assert all(session.closed for session in sessions)


def test_write_request_is_not_retried_after_disconnect(monkeypatch):
    user = SimpleNamespace(id=1, tenant_id=1, is_active=True, tenant=SimpleNamespace(is_active=True), roles=[])
    sessions = []
    handler_calls = 0

    def make_session():
        session = _Session(user)
        sessions.append(session)
        return session

    monkeypatch.setattr(auth_utils, "SessionLocal", make_session)
    monkeypatch.setattr(
        auth_utils,
        "decode_token",
        lambda token: {"sub": 1, "roles": []},
    )
    monkeypatch.setattr(auth_utils.sentry_sdk, "capture_exception", lambda error: None)

    app = Quart(__name__)

    @app.post("/no-retry")
    @auth_utils.requires_auth()
    async def no_retry_route():
        nonlocal handler_calls
        handler_calls += 1
        raise OperationalError(
            "INSERT INTO example VALUES (1)",
            {},
            Exception("server closed the connection unexpectedly"),
            connection_invalidated=True,
        )

    async def exercise_route():
        client = app.test_client()
        response = await client.post(
            "/no-retry", headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 500
        assert await response.get_json() == {"error": "Database error"}

    asyncio.run(exercise_route())

    assert handler_calls == 1
    assert len(sessions) == 1
    assert sessions[0].closed


def test_get_with_committed_activity_is_not_replayed(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    user = SimpleNamespace(id=1, tenant_id=1, roles=[], tenant=SimpleNamespace(is_active=True))
    monkeypatch.setattr(auth_utils, 'SessionLocal', lambda: _Session(user))
    monkeypatch.setattr(auth_utils, 'decode_token', lambda token: {'sub': 1})
    app = Quart(__name__)
    calls = []
    engine = create_engine('sqlite://')

    @app.get('/view')
    @auth_utils.requires_auth()
    async def view():
        calls.append(1)
        with Session(engine) as session:
            session.commit()
        raise OperationalError('SELECT 1', {}, Exception('disconnect'), connection_invalidated=True)

    async def exercise():
        response = await app.test_client().get('/view', headers={'Authorization': 'Bearer test'})
        assert response.status_code == 500

    asyncio.run(exercise())
    assert len(calls) == 1
    engine.dispose()
