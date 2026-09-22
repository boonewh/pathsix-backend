import json
import logging

import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.transport import Transport

from test_read_recovery import exercise, failure


def test_auth_error_survives_sentry_serialization_without_private_data(monkeypatch):
    events = []

    class CaptureTransport(Transport):
        def capture_envelope(self, envelope):
            for item in envelope.items:
                if item.headers.get("type") == "event":
                    events.append(item.payload.json)

    with sentry_sdk.init(
        dsn="https://public@example.invalid/1",
        transport=CaptureTransport,
        default_integrations=False,
        integrations=[LoggingIntegration(level=logging.WARNING, event_level=logging.ERROR)],
        send_default_pii=False,
    ):
        response, sessions, sleeps, calls = exercise(monkeypatch, [failure(False)])
        sentry_sdk.flush()

    assert response[0] == 500 and not calls and not sleeps
    assert len(events) == 1
    event = events[0]
    assert "%s" not in event["logentry"]["message"]
    assert "OperationalError" in event["logentry"]["message"]
    detail = event["extra"]["database_failure"]
    assert detail == {
        "incident": response[1]["request_id"], "endpoint": "handler", "method": "GET",
        "attempt": 1, "exception": "OperationalError", "driver": "RuntimeError",
        "sqlstate": None, "disconnected": False, "retry": False,
    }
    assert "PRIVATE" not in json.dumps(event)
    assert "Bearer test" not in json.dumps(event)
