"""A returning read-only account can discover and explicitly approve write scopes."""
import pytest
from app.models import AIClient, AIConnection, AIWriteAction, Lead
from test_mcp_reads import oauth, rpc, call
from test_mcp_lead_creation import writer, prepare
from test_security_boundaries import crm


@pytest.mark.parametrize('version', ['2025-11-25', '2026-07-28'])
@pytest.mark.parametrize('original_scopes', ['clients:read', 'clients:read leads:read'])
def test_discovery_drives_complete_consent_without_widening_existing_token(oauth, version, original_scopes):
    with oauth.admin() as db:
        db.get(AIClient, 'pilot').allowed_scopes = ['clients:read','leads:read','leads:create']
        db.commit()
    oauth.params['scope'] = original_scopes
    original = oauth.tokens()
    meta = {'_meta':{'io.modelcontextprotocol/protocolVersion':version,
        'io.modelcontextprotocol/clientCapabilities':{}}} if version == '2026-07-28' else {}
    status, body, _ = rpc(oauth, original['access_token'], 'tools/list', meta,
        headers={'MCP-Protocol-Version':version})
    assert status == 200
    tools = {t['name']:t for t in body['result']['tools']}
    scopes = {s for t in tools.values() for scheme in t['_meta']['securitySchemes'] for s in scheme['scopes']}
    assert scopes == {'clients:read', 'leads:read', 'leads:create'}
    assert tools['decide_lead_creation']['_meta']['ui']['visibility'] == ['app']
    assert 'review card' in tools['prepare_lead_creation']['description']
    denied = call(oauth, original['access_token'], 'prepare_lead_creation',
        {'request_key':'read-only-proof-key', 'lead':{'name':'Must not save'}})
    assert denied['isError'] and denied['_meta']['mcp/www_authenticate']
    with oauth.admin() as db:
        assert db.query(AIWriteAction).count() == 0
        assert db.query(Lead).count() == 2
        assert db.query(AIConnection).one().scopes == sorted(original_scopes.split())

    # Use scopes from actual wire discovery, not a hard-coded manual URL upgrade.
    oauth.params['scope'] = ' '.join(sorted(scopes))
    consented = oauth.tokens()
    proposal = prepare(oauth, consented['access_token'])
    assert proposal['status'] == 'pending'
    status, refreshed = oauth.refresh(original['refresh_token'])
    assert status == 200 and set(refreshed['scope'].split()) == set(original_scopes.split())
    assert call(oauth, refreshed['access_token'], 'get_lead_creation',
        {'action_id':proposal['action_id']})['isError']
    with oauth.admin() as db: assert db.query(Lead).count() == 2


def test_catalog_restrictions_still_limit_discovery(oauth):
    token = oauth.tokens()['access_token']
    assert {t['name'] for t in rpc(oauth, token)[1]['result']['tools']} == {'list_clients','get_client'}
    with oauth.admin() as db:
        db.get(AIClient, 'pilot').allowed_scopes = ['clients:read','leads:read']
        db.commit()
    names = {t['name'] for t in rpc(oauth, token)[1]['result']['tools']}
    assert names == {'list_clients','get_client','list_leads','get_lead','get_lead_options'}
    assert call(oauth, token, 'list_leads')['isError']


@pytest.mark.parametrize('invalid', ['expired', 'revoked'])
def test_unusable_connections_cannot_discover_tools(oauth, invalid):
    from datetime import datetime, timedelta
    from app.models import OAuthCredential
    writer(oauth)
    original = oauth.tokens()
    with oauth.admin() as db:
        if invalid == 'expired':
            for row in db.query(OAuthCredential).filter_by(kind='access'):
                row.expires_at = datetime.utcnow() - timedelta(seconds=1)
        else:
            for row in db.query(AIConnection): row.revoked_at = datetime.utcnow()
        db.commit()
    assert rpc(oauth, original['access_token'])[0] == 401
