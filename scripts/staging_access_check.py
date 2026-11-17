"""Agent-run staging check: insufficient and revoked AI access are refused live.

Run from the operator's workstation (Windows, signed in to Fly):
    python scripts/staging_access_check.py

1. Registers a dedicated test client through the public DCR endpoint.
2. Creates a read-only grant and short-lived access token for the staging admin
   user directly in the database (the agent cannot sign in as the user). Only
   the token's SHA-256 hash crosses to the server; the raw token stays local.
3. Calls live /mcp: reads must work; a lead write must be refused with a
   scope-upgrade challenge.
4. Revokes the grant, then confirms every call is refused (HTTP 401).
The revoked grant remains as named evidence. Prints only outcomes, never tokens.
"""
import base64
import hashlib
import json
import os
import secrets
import subprocess
import sys
import urllib.error
import urllib.request

BASE = 'https://api-staging.pathsix.app'
APP = 'pathsixsolutions-backend-staging'
ADMIN_URL_HELPER = os.getenv('PATHSIX_STAGING_ADMIN_URL_HELPER', '')
CLIENT_NAME = 'PathSix agent access check'

REMOTE = r'''
import sys, json
from datetime import datetime, timedelta
from uuid import uuid4
from sqlalchemy import create_engine, text
url = sys.stdin.readline().strip().replace('postgres://', 'postgresql://', 1)
if 'pathsixsolutions-db-staging' not in url:
    raise SystemExit('Refusing unexpected database')
args = json.loads(sys.argv[1])
engine = create_engine(url, hide_parameters=True)
with engine.begin() as c:
    if args['step'] == 'grant':
        user = c.execute(text("SELECT id, tenant_id FROM public.users WHERE email='admin@staging.test'")).one()
        now = datetime.utcnow(); ident = str(uuid4())
        c.execute(text("INSERT INTO public.ai_connections (id, tenant_id, user_id, client_id, resource, scopes, created_at, expires_at, active_until) "
                       "VALUES (:id, :t, :u, :c, :r, '[\"clients:read\"]', :now, :exp, :exp)"),
                  {'id': ident, 't': user.tenant_id, 'u': user.id, 'c': args['client'], 'r': args['resource'], 'now': now, 'exp': now + timedelta(days=1)})
        c.execute(text("INSERT INTO public.oauth_credentials (token_hash, tenant_id, user_id, connection_id, kind, scopes, created_at, expires_at) "
                       "VALUES (:h, :t, :u, :id, 'access', '[\"clients:read\"]', :now, :exp)"),
                  {'h': args['hash'], 't': user.tenant_id, 'u': user.id, 'id': ident, 'now': now, 'exp': now + timedelta(minutes=10)})
        print(json.dumps({'grant': ident}))
    elif args['step'] == 'revoke':
        n = c.execute(text("UPDATE public.ai_connections SET revoked_at=:now WHERE id=:id AND revoked_at IS NULL"),
                      {'id': args['grant'], 'now': datetime.utcnow()}).rowcount
        print(json.dumps({'revoked': n}))
engine.dispose()
'''


def remote(step):
    encoded = base64.b64encode(REMOTE.encode()).decode()
    # Both the step and the code travel as base64 so no nested quoting is needed.
    payload = base64.b64encode(json.dumps(step).encode()).decode()
    command = (f"python -c \"import base64,sys;sys.argv=['r',base64.b64decode('{payload}').decode()];"
               f"exec(base64.b64decode('{encoded}'))\"")
    helper = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ADMIN_URL_HELPER],
                            capture_output=True, check=True)
    result = subprocess.run(['flyctl', 'ssh', 'console', '--app', APP, '-C', command],
                            input=helper.stdout, capture_output=True)
    lines = [line for line in result.stdout.decode(errors='replace').splitlines() if line.startswith('{')]
    if not lines:
        raise SystemExit(f'Remote step {step["step"]} failed')
    return json.loads(lines[-1])


def http(method, path, body=None, token=None):
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream',
               'MCP-Protocol-Version': '2025-11-25'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    request = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read() or b'null')
    except urllib.error.HTTPError as error:
        return error.code, None


def rpc(token, method, params):
    return http('POST', '/mcp', {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}, token)


def main():
    if not ADMIN_URL_HELPER:
        raise SystemExit('Set PATHSIX_STAGING_ADMIN_URL_HELPER to the admin URL helper script')
    results = {}
    status, client = http('POST', '/oauth/register', {'client_name': CLIENT_NAME, 'redirect_uris': ['http://127.0.0.1/callback']})
    assert status == 201, status
    raw = secrets.token_urlsafe(32)
    grant = remote({'step': 'grant', 'client': client['client_id'], 'resource': BASE + '/mcp',
                    'hash': hashlib.sha256(raw.encode('ascii')).hexdigest()})['grant']

    status, listing = rpc(raw, 'tools/list', {})
    names = sorted(tool['name'] for tool in listing['result']['tools'])
    results['read_only_tools_listed'] = names
    status, read = rpc(raw, 'tools/call', {'name': 'list_clients', 'arguments': {'limit': 1}})
    results['read_allowed'] = status == 200 and not read['result'].get('isError')
    status, write = rpc(raw, 'tools/call', {'name': 'prepare_lead_creation', 'arguments': {
        'request_key': secrets.token_urlsafe(16), 'lead': {'name': 'MUST-NOT-BE-CREATED'}}})
    challenge = (write['result'].get('_meta') or {}).get('mcp/www_authenticate', [''])[0]
    results['write_refused'] = write['result'].get('isError') is True
    results['write_refusal_requests_upgrade'] = 'insufficient_scope' in challenge and 'leads:create' in challenge

    results['revoked'] = remote({'step': 'revoke', 'grant': grant})['revoked'] == 1
    results['after_revoke_list_status'] = rpc(raw, 'tools/list', {})[0]
    results['after_revoke_call_status'] = rpc(raw, 'tools/call', {'name': 'list_clients', 'arguments': {}})[0]

    passed = (results['read_allowed'] and results['write_refused'] and results['write_refusal_requests_upgrade']
              and 'prepare_lead_creation' in names and results['revoked']
              and results['after_revoke_list_status'] == 401 and results['after_revoke_call_status'] == 401)
    print(json.dumps({'grant': grant, 'client': client['client_id'], 'passed': passed, **results}, indent=1))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
