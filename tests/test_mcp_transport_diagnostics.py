"""Timing evidence must not expose credentials, CRM payloads or exception text."""
import asyncio
import json
import logging

import pytest
from authlib.oauth2.rfc6749.errors import InvalidGrantError
from app import mcp_server


@pytest.mark.parametrize('outcome', ['success', 'denied', 'exception'])
def test_transport_diagnostics_are_bounded_and_private(monkeypatch, caplog, outcome):
    secret = 'private-token-and-crm-data'
    monkeypatch.setattr(mcp_server, 'configured_resource', lambda: 'https://crm.test/mcp')
    monkeypatch.setattr(mcp_server, 'metadata_url', lambda: 'https://crm.test/metadata')
    def authenticate(*args):
        if outcome == 'denied':
            raise InvalidGrantError(description=secret)
    monkeypatch.setattr(mcp_server.mcp_reads, 'authenticate', authenticate)
    messages = []
    async def sdk(scope, receive, send):
        await receive()
        if outcome == 'exception':
            raise RuntimeError(secret)
        await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        await send({'type': 'http.response.body', 'body': secret.encode()})
    async def receive():
        return {'type': 'http.request', 'body': json.dumps({'secret': secret}).encode()}
    async def send(message):
        messages.append(message)
    scope = {'type': 'http', 'path': '/mcp', 'method': 'POST', 'headers': [
        (b'host', b'crm.test'), (b'authorization', ('Bearer '+secret).encode()),
        (b'content-type', b'application/json')]}
    caplog.set_level(logging.INFO, logger='pathsix.mcp_transport')
    gateway = mcp_server.Gateway(None, sdk)
    if outcome == 'exception':
        with pytest.raises(RuntimeError, match=secret):
            asyncio.run(gateway(scope, receive, send))
    else:
        asyncio.run(gateway(scope, receive, send))
        assert messages[0]['status'] == (401 if outcome == 'denied' else 200)
    logs = [r.getMessage() for r in caplog.records if r.name == 'pathsix.mcp_transport']
    assert len(logs) == 2
    assert logs[0].startswith('MCP request started id=')
    assert logs[0].split('id=')[1] in logs[1]
    assert 'duration_ms=' in logs[1] and 'auth_ms=' in logs[1]
    assert ('phase=authentication' if outcome == 'denied' else 'phase=sdk') in logs[1]
    assert secret not in caplog.text and 'Bearer' not in caplog.text
    assert 'RuntimeError' not in caplog.text
