import os
os.environ.setdefault("SECRET_KEY", "isolated-test-only")
os.environ.setdefault("DATABASE_URL", "sqlite:///recovery-test-unused.db")

import asyncio
from types import SimpleNamespace
import pytest
from quart import Quart
from sqlalchemy.exc import OperationalError
from app.database import SessionLocal
from app.utils import auth_utils


def failure(disconnected=True):
    return OperationalError("PRIVATE SQL", {"secret": "PRIVATE PARAMETER"},
                            RuntimeError("PRIVATE DRIVER MESSAGE"), connection_invalidated=disconnected)


def exercise(monkeypatch, outcomes, method="GET", handler_error=None):
    app = Quart(__name__)
    sessions, sleeps, calls = [], [], []

    class Session:
        def __init__(self):
            self.closed = False
            self.outcome = outcomes[len(sessions)]
            sessions.append(self)
        def query(self, *args): return self
        def options(self, *args): return self
        def filter(self, *args): return self
        def first(self):
            if isinstance(self.outcome, Exception): raise self.outcome
            return self.outcome
        def close(self): self.closed = True

    async def sleep(delay):
        assert all(session.closed for session in sessions)
        sleeps.append(delay)

    monkeypatch.setattr(auth_utils, "SessionLocal", Session)
    monkeypatch.setattr(auth_utils, "decode_token", lambda token: {"sub": 1, "roles": ["admin"]})
    monkeypatch.setattr(auth_utils.asyncio, "sleep", sleep)

    @app.route("/check", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"])
    @auth_utils.requires_auth()
    async def handler():
        assert all(session.closed for session in sessions)
        calls.append(True)
        if handler_error: raise handler_error
        return {"ok": True}

    async def run():
        response = await app.test_client().open("/check", method=method, headers={"Authorization": "Bearer test"})
        return response.status_code, await response.get_json()

    return asyncio.run(run()), sessions, sleeps, calls


USER = SimpleNamespace(id=1, roles=[])


def test_session_factory_never_shares_sessions_on_same_thread():
    first, second = SessionLocal(), SessionLocal()
    try:
        assert first is not second
    finally:
        first.close()
        second.close()


def test_dropped_auth_connection_retries_once_with_fresh_session(monkeypatch):
    response, sessions, sleeps, calls = exercise(monkeypatch, [failure(), USER])
    assert response == (200, {"ok": True})
    assert len(sessions) == 2 and all(s.closed for s in sessions)
    assert sleeps == [0.25] and len(calls) == 1


def test_exhausted_retry_is_bounded_and_logs_no_private_details(monkeypatch, caplog):
    response, sessions, sleeps, calls = exercise(monkeypatch, [failure(), failure()])
    assert response[0] == 503 and response[1]["retryable"] is True
    assert response[1]["request_id"]
    assert len(sessions) == 2 and sleeps == [0.25] and not calls
    assert "OperationalError" in caplog.text and "disconnected=True" in caplog.text
    assert "PRIVATE" not in caplog.text and "PRIVATE" not in str(response)


def test_non_disconnect_database_error_is_not_retried(monkeypatch):
    response, sessions, sleeps, calls = exercise(monkeypatch, [failure(False)])
    assert response[0] == 500 and response[1]["retryable"] is False
    assert len(sessions) == 1 and not sleeps and not calls


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_mutations_are_not_retried(monkeypatch, method):
    response, sessions, sleeps, calls = exercise(monkeypatch, [failure()], method)
    assert response[0] == 503
    assert len(sessions) == 1 and not sleeps and not calls


def test_route_handler_is_never_replayed_even_for_get(monkeypatch):
    response, sessions, sleeps, calls = exercise(monkeypatch, [USER], handler_error=failure())
    assert response[0] == 500 and len(calls) == 1
    assert len(sessions) == 1 and not sleeps


def test_missing_user_does_not_enter_handler(monkeypatch):
    response, sessions, sleeps, calls = exercise(monkeypatch, [None])
    assert response[0] == 401 and not sleeps and not calls
    assert sessions[0].closed
