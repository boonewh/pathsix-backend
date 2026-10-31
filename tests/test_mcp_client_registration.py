"""Self-registered AI clients (CIMD/DCR), loopback redirects and tolerant requests."""
import html
import json
import re
import secrets
import socket
from datetime import datetime, timedelta
from urllib.parse import urlencode, urlsplit, parse_qs
from uuid import uuid4
import pytest
from authlib.oauth2.rfc7636.challenge import create_s256_code_challenge
from app.models import AIClient, AIConnection, OAuthCredential
from app.services import client_registration, mcp_reads
from app.services.ai_connections import PUBLISHED_SCOPES
from app.services.client_metadata import ClientMetadataError, is_metadata_client_id, parse, public_address
from app.services.oauth import digest
from app.utils.rate_limiter import reset_rate_limit
from migrations.versions.mcp_client_registration import PUBLISHED as MIGRATION_PUBLISHED
from test_security_boundaries import crm
from test_oauth import oauth as oauth_fixture, RESOURCE, BASE
from test_mcp_reads import oauth, rpc

CIMD = 'https://agent.example/oauth/client.json'
CIMD_REDIRECT = 'https://agent.example/callback'
LOOPBACK = 'http://127.0.0.1/callback'


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv('MCP_CLIENT_REGISTRATION', '1')
    fetched = []
    def fetcher(client_id):
        fetched.append(client_id)
        return {'name': 'Agent <App>', 'redirect_uris': [CIMD_REDIRECT, LOOPBACK]}
    monkeypatch.setattr(client_registration, 'fetch', fetcher)
    return fetched


def authorize(flow, **params):
    query = dict(response_type='code', redirect_uri=CIMD_REDIRECT, state='s',
                 code_challenge=create_s256_code_challenge(flow.verifier), code_challenge_method='S256')
    query.update(params)
    query = {k: v for k, v in query.items() if v is not None}
    return flow.request('GET', '/oauth/authorize?' + urlencode(query))


def approve(flow, body):
    flow.intent = html.unescape(re.search(r'data-intent="([^"]+)"', body)[1])
    status, decided, _ = flow.decision()
    assert status == 200, decided
    return parse_qs(urlsplit(json.loads(decided)['redirect']).query)


def exchange(flow, client_id, code, redirect, **extra):
    data = dict(grant_type='authorization_code', client_id=client_id, code=code,
                code_verifier=flow.verifier, redirect_uri=redirect, **extra)
    return flow.form('/oauth/token', data)


def metadata(flow, path):
    async def read():
        response = await flow.app.test_client().get(path, scheme='https')
        return response.status_code, await response.get_json()
    return flow.runner.run(read())


def test_published_scopes_match_tools_and_database_function():
    assert PUBLISHED_SCOPES == {s for scopes in mcp_reads.TOOL_SCOPES.values() for s in scopes}
    assert set(MIGRATION_PUBLISHED) == PUBLISHED_SCOPES


def test_metadata_advertises_registration_only_when_enabled(oauth, monkeypatch):
    status, server = metadata(oauth, '/.well-known/oauth-authorization-server')
    assert status == 200 and 'registration_endpoint' not in server
    assert 'client_id_metadata_document_supported' not in server
    _, resource = metadata(oauth, '/.well-known/oauth-protected-resource/mcp')
    assert server['scopes_supported'] == resource['scopes_supported'] == sorted(PUBLISHED_SCOPES)
    monkeypatch.setenv('MCP_CLIENT_REGISTRATION', '1')
    _, server = metadata(oauth, '/.well-known/oauth-authorization-server')
    assert server['registration_endpoint'] == BASE + '/oauth/register'
    assert server['client_id_metadata_document_supported'] is True


def test_missing_scope_resource_and_state_use_safe_defaults(oauth):
    with oauth.admin() as db:
        db.get(AIClient, 'pilot').allowed_scopes = ['clients:read', 'leads:read', 'reports:read']
        db.commit()
    status, body, _ = authorize(oauth, client_id='pilot', redirect_uri=oauth.params['redirect_uri'],
                                state=None, prompt='consent', nonce='ignored-value')
    assert status == 200, body
    # Only published scopes the client is allowed, never reports:read.
    assert 'Read clients you can access' in body and 'Read leads you can access' in body
    assert 'company reports' not in body and 'ignored-value' not in body
    params = approve(oauth, body)
    assert 'state' not in params and params['iss'] == [BASE]
    status, token = exchange(oauth, 'pilot', params['code'][0], oauth.params['redirect_uri'], unexpected='x')
    assert status == 200, token
    assert token['scope'] == 'clients:read leads:read' and token['resource'] == RESOURCE


@pytest.mark.parametrize('change', [{'resource': 'https://wrong.test/mcp'}, {'state': ''},
                                    {'code_challenge_method': 'plain'}, {'client_id': None}])
def test_tolerance_keeps_core_checks(oauth, change):
    status, _, headers = authorize(oauth, **{'client_id': 'pilot', 'redirect_uri': oauth.params['redirect_uri'], **change})
    assert status in (400, 401) and 'Location' not in headers


def test_token_rejects_wrong_resource_but_accepts_omitted(oauth):
    code = oauth.code()
    assert oauth.exchange(code, resource='https://wrong.test/mcp')[0] == 400


def register(flow, body):
    status, response, _ = flow.request('POST', '/oauth/register', json=body)
    return status, json.loads(response)


def test_dcr_registers_public_client_and_loopback_port_flexes(oauth, enabled):
    status, client = register(oauth, {'client_name': 'Desk <Tool>', 'redirect_uris': ['http://localhost/callback'],
                                      'token_endpoint_auth_method': 'client_secret_basic',
                                      'grant_types': ['authorization_code', 'refresh_token']})
    assert status == 201, client
    assert client['token_endpoint_auth_method'] == 'none' and 'client_secret' not in client
    assert client['client_id'].startswith('dcr-') and client['scope'] == ' '.join(sorted(PUBLISHED_SCOPES))
    with oauth.admin() as db:
        row = db.get(AIClient, client['client_id'])
        assert row.registration == 'dcr' and row.inline_review is False and set(row.allowed_scopes) == PUBLISHED_SCOPES
    runtime = 'http://localhost:53111/callback'
    status, body, _ = authorize(oauth, client_id=client['client_id'], redirect_uri=runtime, scope='leads:read')
    assert status == 200, body
    assert 'has not verified that name' in body and 'Desk &lt;Tool&gt;' in body and 'localhost:53111' in body
    params = approve(oauth, body)
    status, token = exchange(oauth, client['client_id'], params['code'][0], runtime)
    assert status == 200 and token['scope'] == 'leads:read'
    # Port flexibility never extends to another host or path.
    for other in ('http://localhost:53111/other', 'http://127.0.0.1:53111/callback', 'http://evil.test/callback'):
        assert authorize(oauth, client_id=client['client_id'], redirect_uri=other, scope='leads:read')[0] in (400, 401)


@pytest.mark.parametrize('body,error', [
    ({'redirect_uris': ['javascript:alert(1)']}, 'invalid_redirect_uri'),
    ({'redirect_uris': ['http://evil.test/cb']}, 'invalid_redirect_uri'),
    ({'redirect_uris': []}, 'invalid_redirect_uri'),
    ({}, 'invalid_redirect_uri'),
    ({'redirect_uris': [LOOPBACK], 'grant_types': ['client_credentials']}, 'invalid_client_metadata'),
    ({'redirect_uris': [LOOPBACK], 'client_name': 'x' * 101}, 'invalid_client_metadata'),
    (['not', 'an', 'object'], 'invalid_client_metadata')])
def test_dcr_rejects_unsafe_metadata(oauth, enabled, body, error):
    assert register(oauth, body) == (400, {'error': error})
    with oauth.admin() as db:
        assert db.query(AIClient).filter_by(registration='dcr').count() == 0


def test_dcr_is_disabled_by_default_and_rate_limited(oauth, monkeypatch):
    assert register(oauth, {'redirect_uris': [LOOPBACK]})[0] == 404
    monkeypatch.setenv('MCP_CLIENT_REGISTRATION', '1')
    reset_rate_limit()
    statuses = [register(oauth, {'redirect_uris': [LOOPBACK]})[0] for _ in range(11)]
    assert statuses == [201] * 10 + [429]


def test_stale_unused_dcr_registrations_are_removed(oauth, enabled):
    old = datetime.utcnow() - timedelta(days=2)
    with oauth.admin() as db:
        for ident in ('dcr-old-unused', 'dcr-old-used'):
            db.add(AIClient(id=ident, name='Old', is_active=True, oauth_enabled=True, allowed_scopes=['clients:read'],
                            redirect_uris=[LOOPBACK], registration='dcr', created_at=old))
        db.flush()
        db.add(AIConnection(id=str(uuid4()), tenant_id=1, user_id=1, client_id='dcr-old-used', resource=RESOURCE,
                            scopes=['clients:read'], expires_at=datetime.utcnow() + timedelta(days=1)))
        db.commit()
    assert register(oauth, {'redirect_uris': [LOOPBACK]})[0] == 201
    with oauth.admin() as db:
        assert db.get(AIClient, 'dcr-old-unused') is None and db.get(AIClient, 'dcr-old-used') is not None


def test_cimd_client_connects_without_registration_and_uses_review_link(oauth, enabled):
    status, body, _ = authorize(oauth, client_id=CIMD, scope='leads:read leads:create')
    assert status == 200, body
    assert enabled == [CIMD] and 'published by <strong>agent.example</strong>' in body
    assert 'Agent &lt;App&gt;' in body
    params = approve(oauth, body)
    status, token = exchange(oauth, CIMD, params['code'][0], CIMD_REDIRECT)
    assert status == 200, token
    with oauth.admin() as db:
        row = db.get(AIClient, CIMD)
        assert row.registration == 'cimd' and row.inline_review is False
    status, listing, _ = rpc(oauth, token['access_token'])
    tools = {tool['name']: tool for tool in listing['result']['tools']}
    assert 'decide_lead_creation' not in tools
    assert 'ui' not in tools['prepare_lead_creation'].get('_meta', {})
    assert 'review_url' in tools['prepare_lead_creation']['description']
    # Even a client that guesses the app-only tool cannot redeem a card decision.
    value, error = mcp_reads.execute(oauth.admin, token['access_token'], 'decide_lead_creation',
                                     {'action_id': str(uuid4()), 'intent': 'x', 'approved': True}, review=object())
    assert value is None and error == 'Required permission is not available'


def test_cimd_metadata_is_cached_then_refreshed(oauth, enabled):
    assert authorize(oauth, client_id=CIMD)[0] == 200
    assert authorize(oauth, client_id=CIMD)[0] == 200
    assert enabled == [CIMD]
    with oauth.admin() as db:
        db.get(AIClient, CIMD).metadata_fetched_at = datetime.utcnow() - timedelta(days=2)
        db.commit()
    assert authorize(oauth, client_id=CIMD)[0] == 200
    assert enabled == [CIMD, CIMD]


def test_cimd_failures_and_disabled_registration_fail_closed(oauth, monkeypatch):
    calls = []
    def broken(client_id):
        calls.append(client_id)
        raise ClientMetadataError('unavailable')
    monkeypatch.setattr(client_registration, 'fetch', broken)
    assert authorize(oauth, client_id=CIMD)[0] in (400, 401) and calls == []
    monkeypatch.setenv('MCP_CLIENT_REGISTRATION', '1')
    assert authorize(oauth, client_id=CIMD)[0] in (400, 401) and calls == [CIMD]
    with oauth.admin() as db:
        assert db.get(AIClient, CIMD) is None


def test_self_registration_never_replaces_operator_client(oauth, enabled):
    with oauth.admin() as db:
        db.add(AIClient(id=CIMD, name='Operator', is_active=True, oauth_enabled=True, inline_review=True,
                        allowed_scopes=['clients:read'], redirect_uris=[CIMD_REDIRECT]))
        db.commit()
    assert authorize(oauth, client_id=CIMD, scope='clients:read')[0] == 200
    assert enabled == []
    with pytest.raises(client_registration.RegistrationUnavailable):
        client_registration.store(oauth.admin, CIMD, 'Hijack', [LOOPBACK], 'cimd')
    with oauth.admin() as db:
        row = db.get(AIClient, CIMD)
        assert (row.name, row.registration, row.inline_review) == ('Operator', 'operator', True)


@pytest.mark.parametrize('value,valid', [
    (CIMD, True), ('https://agent.example', False), ('https://agent.example/', False),
    ('http://agent.example/client.json', False), ('https://agent.example/c.json?x=1', False),
    ('https://agent.example/c.json#f', False), ('https://user@agent.example/c.json', False),
    ('https://agent.example:8443/c.json', False), ('https://agent.example/../c.json', False),
    ('https://agent.example/' + 'a' * 200, False), ('pilot', False), (None, False)])
def test_metadata_client_id_shape(value, valid):
    assert is_metadata_client_id(value) is valid


def resolver(*addresses):
    return lambda host, port, type=None: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (a, port)) for a in addresses]


@pytest.mark.parametrize('addresses', [('127.0.0.1',), ('10.0.0.5',), ('169.254.169.254',), ('192.168.1.1',),
                                       ('93.184.216.34', '10.0.0.5'), ('fdaa::3',), ('::1',), ()])
def test_metadata_fetch_refuses_non_public_addresses(addresses):
    with pytest.raises(ClientMetadataError):
        public_address('agent.example', resolver(*addresses))


def test_metadata_fetch_refuses_ip_literals_and_pins_public_address():
    for host in ('93.184.216.34', '[2606:2800:220:1::]'):
        with pytest.raises(ClientMetadataError):
            public_address(host, resolver('93.184.216.34'))
    assert public_address('agent.example', resolver('93.184.216.34')) == '93.184.216.34'


@pytest.mark.parametrize('document', [
    {'client_id': 'https://other.example/c.json', 'redirect_uris': [CIMD_REDIRECT]},
    {'client_id': CIMD, 'redirect_uris': ['http://evil.test/cb']},
    {'client_id': CIMD, 'redirect_uris': [CIMD_REDIRECT], 'token_endpoint_auth_method': 'private_key_jwt'},
    {'client_id': CIMD, 'redirect_uris': [CIMD_REDIRECT], 'response_types': ['token']},
    {'client_id': CIMD, 'redirect_uris': [CIMD_REDIRECT], 'client_name': '\x1b[31m'},
    {'client_id': CIMD}])
def test_metadata_document_validation(document):
    with pytest.raises(ClientMetadataError):
        parse(CIMD, json.dumps(document).encode())


def test_metadata_document_rejects_duplicate_keys_and_defaults_name():
    with pytest.raises(ClientMetadataError):
        parse(CIMD, ('{"client_id":"%s","client_id":"%s","redirect_uris":["%s"]}' % (CIMD, CIMD, CIMD_REDIRECT)).encode())
    parsed = parse(CIMD, json.dumps({'client_id': CIMD, 'redirect_uris': [CIMD_REDIRECT]}).encode())
    assert parsed == {'name': 'agent.example', 'redirect_uris': [CIMD_REDIRECT]}


def test_connect_page_gives_one_url_for_any_ai_app(oauth, monkeypatch):
    status, body, headers = oauth.request('GET', '/connect')
    assert status == 200 and f'<code>{RESOURCE}</code>' in body
    assert "frame-ancestors 'none'" in headers['Content-Security-Policy'] and headers['Cache-Control'] == 'no-store'
    for app in ('Claude', 'ChatGPT', 'Gemini', 'Claude Code', 'VS Code'):
        assert f'<h3>{app}</h3>' in body
    assert 'client ID or secret' in body and '90 days without use' in body and '<script' not in body
    monkeypatch.setenv('MCP_RESOURCE_URI', 'http://insecure.test/mcp')
    assert oauth.request('GET', '/connect')[0] == 503


def test_user_facing_copy_does_not_assume_one_ai_host():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / 'app'
    for path in ('templates/mcp_lead_review.html', 'templates/ai_action.html', 'static/ai_action.js',
                 'services/ai_actions.py', 'templates/oauth.html'):
        assert 'ChatGPT' not in (root / path).read_text(encoding='utf-8'), path
