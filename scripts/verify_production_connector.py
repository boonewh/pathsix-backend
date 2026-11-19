"""Agent-run production check: the deployed AI connector reads and writes correctly.

Run from the operator's workstation (Windows, signed in to Fly):
    python scripts/verify_production_connector.py [--keep-tenant]

Every write lands in an isolated synthetic tenant created for this check, never
in a customer tenant. The production administrator URL is read by the DPAPI
helper and piped into the production machine on stdin; it never reaches a
terminal, an argument list or this process's output. Generated passwords travel
the same way. Only outcomes are printed.

The check proves, against https://api.pathsix.app/mcp:
  1. Discovery metadata agrees with the authorization server.
  2. An unauthenticated call is refused and names its resource metadata.
  3. A self-registered (DCR) client connects and reads the synthetic records.
  4. A read-only grant cannot write and is told which scope to request.
  5. A write is proposed, reviewed in the browser, approved and saved once.
  6. A second proposal is cancelled and saves nothing.
  7. A self-registered client never receives the in-chat decide tool.
  8. The connection sees only its own tenant's records.
  9. Revocation refuses every later call.
"""
import argparse
import base64
import hashlib
import http.cookiejar
import json
import os
import secrets
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta

TARGETS = {
    # Rehearse on staging first, exactly as the production release was rehearsed.
    'staging': {
        'base': 'https://api-staging.pathsix.app',
        'app': 'pathsixsolutions-backend-staging',
        'database': 'pathsixsolutions_backend_staging',
        'admin_user': 'pathsixsolutions_backend_staging',
        'hosts': ['pathsixsolutions-db-staging.flycast', 'pathsixsolutions-db-staging.internal'],
        'helper_env': 'PATHSIX_STAGING_ADMIN_URL_HELPER',
        'helper_default': r'G:\Projects\tools\pathsix-staging-admin-url.ps1',
    },
    'production': {
        'base': 'https://api.pathsix.app',
        'app': 'pathsixsolutions-backend',
        'database': 'pathsixsolutions_backend',
        'admin_user': 'pathsixsolutions_backend',
        'hosts': ['pathsixsolutions-db.flycast', 'pathsixsolutions-db.internal'],
        'helper_env': 'PATHSIX_PRODUCTION_ADMIN_URL_HELPER',
        'helper_default': r'G:\Projects\tools\pathsix-prod-admin-url.ps1',
    },
}
TENANT_SLUG = 'mcp-verify'
TENANT_NAME = 'PathSix Connector Verification'
USER_EMAIL = 'connector-check@pathsix.test'
CLIENT_NAME = 'PathSix connector release check'
WRITE_SCOPES = ['clients:read', 'leads:read', 'leads:create', 'interactions:read', 'projects:read', 'contacts:read']
READ_SCOPES = ['clients:read', 'leads:read']

# Runs inside the production machine. The administrator URL is line 1 of stdin
# and the step's secrets are line 2, so neither appears in an argument list.
REMOTE = r'''
import json, sys
from datetime import datetime, timedelta
sys.path.insert(0, '/app')
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

url = sys.stdin.readline().strip().replace('postgres://', 'postgresql://', 1)
secretsin = json.loads(sys.stdin.readline() or '{}')
args = json.loads(sys.argv[1])
spec = args['target']
parsed = make_url(url)
if (parsed.host not in spec['hosts'] or parsed.database != spec['database']
        or parsed.username != spec['admin_user']):
    raise SystemExit('Refusing unexpected database target')
engine = create_engine(url, hide_parameters=True)
SLUG, EMAIL = 'mcp-verify', 'connector-check@pathsix.test'


def tenant_and_user(c):
    row = c.execute(text('SELECT id FROM public.tenants WHERE slug=:s'), {'s': SLUG}).one_or_none()
    tenant = row.id if row else None
    row = c.execute(text('SELECT id FROM public.users WHERE email=:e'), {'e': EMAIL}).one_or_none()
    return tenant, (row.id if row else None)


def live_only(c, table):
    """Tombstone filter, only where the target schema has it (mobile_tombstones).

    Production may run this before or after those columns arrive; a tombstoned
    fixture must never be reused once they exist.
    """
    present = c.execute(text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' "
        "AND table_name=:t AND column_name='deleted_at')"), {'t': table}).scalar_one()
    return ' AND deleted_at IS NULL' if present else ''


with engine.begin() as c:
    c.execute(text("SET LOCAL statement_timeout='30s'"))
    database, user = c.execute(text('SELECT current_database(), current_user')).one()
    if (database, user) != (spec['database'], spec['admin_user']):
        raise SystemExit('Unexpected database identity')
    step = args['step']

    if step == 'setup':
        from app.utils.auth_utils import hash_password
        tenant, uid = tenant_and_user(c)
        customer_tenants = c.execute(text('SELECT count(*) FROM public.tenants WHERE slug<>:s'), {'s': SLUG}).scalar_one()
        if tenant is None:
            tenant = c.execute(text('INSERT INTO public.tenants (name, slug, is_active, created_at, config) '
                                    'VALUES (:n, :s, true, :now, CAST(:cfg AS json)) RETURNING id'),
                               {'n': args['tenant_name'], 's': SLUG, 'now': datetime.utcnow(),
                                'cfg': json.dumps(args['config'])}).scalar_one()
        if uid is None:
            uid = c.execute(text('INSERT INTO public.users (email, password_hash, tenant_id, is_active, created_at) '
                                 'VALUES (:e, :p, :t, true, :now) RETURNING id'),
                            {'e': EMAIL, 'p': hash_password(secretsin['password']), 't': tenant,
                             'now': datetime.utcnow()}).scalar_one()
            role = c.execute(text("SELECT id FROM public.roles WHERE name='user'")).one_or_none()
            if role is None:
                raise SystemExit('The ordinary user role is missing')
            c.execute(text('INSERT INTO public.user_roles (user_id, role_id) VALUES (:u, :r)'),
                      {'u': uid, 'r': role.id})
        else:
            c.execute(text('UPDATE public.users SET password_hash=:p, is_active=true WHERE id=:u'),
                      {'p': hash_password(secretsin['password']), 'u': uid})
        if c.execute(text('SELECT tenant_id FROM public.users WHERE id=:u'), {'u': uid}).scalar_one() != tenant:
            raise SystemExit('The check user must belong to the synthetic tenant')
        if c.execute(text("SELECT count(*) FROM public.user_roles ur JOIN public.roles r ON r.id=ur.role_id "
                          "WHERE ur.user_id=:u AND r.name='admin'"), {'u': uid}).scalar_one():
            raise SystemExit('The check user must be an ordinary user')

        owned = {'t': tenant, 'u': uid, 'now': datetime.utcnow()}
        client = c.execute(text('SELECT id FROM public.clients WHERE tenant_id=:t AND name=:name'),
                           {'t': tenant, 'name': 'Verification Client Co.'}).scalar_one_or_none()
        if client is None:
            client = c.execute(text(
                'INSERT INTO public.clients (tenant_id, created_by, assigned_to, name, contact_person, email, phone, '
                'city, state, status, type, notes, created_at) VALUES (:t, :u, :u, :name, :person, :email, :phone, '
                ":city, 'TX', 'active', 'Technology', :notes, :now) RETURNING id"),
                {**owned, 'name': 'Verification Client Co.', 'person': 'Dana Reed', 'email': 'dana@verification.example.com',
                 'phone': '+15125550143', 'city': 'Austin',
                 'notes': 'Synthetic record for the production connector check.'}).scalar_one()
        lead = c.execute(text('SELECT id FROM public.leads WHERE tenant_id=:t AND name=:name'),
                         {'t': tenant, 'name': 'Verification Lead LLC'}).scalar_one_or_none()
        if lead is None:
            lead = c.execute(text(
                'INSERT INTO public.leads (tenant_id, created_by, assigned_to, name, contact_person, email, phone, '
                'city, state, lead_status, lead_source, type, notes, created_at) VALUES (:t, :u, :u, :name, :person, '
                ":email, :phone, :city, 'TX', 'open', 'Referral', 'Technology', :notes, :now) RETURNING id"),
                {**owned, 'name': 'Verification Lead LLC', 'person': 'Avery Stone', 'email': 'avery@verification.example.com',
                 'phone': '+15125550188', 'city': 'Round Rock', 'notes': 'Synthetic record for the connector check.'}
            ).scalar_one()
        interaction = c.execute(text('SELECT id FROM public.interactions WHERE tenant_id=:t AND summary=:s'
                                     + live_only(c, 'interactions')),
                                {'t': tenant, 's': 'Verification check-in'}).scalar_one_or_none()
        if interaction is None:
            interaction = c.execute(text(
                'INSERT INTO public.interactions (tenant_id, client_id, summary, outcome, contact_person, '
                "contact_date, follow_up, followup_status) VALUES (:t, :c, :s, :o, 'Dana Reed', "
                ":now, :soon, 'pending') RETURNING id"),
                {'t': owned['t'], 'now': owned['now'], 'c': client, 's': 'Verification check-in',
                 'o': 'Synthetic interaction for the check.',
                 'soon': datetime.utcnow() + timedelta(days=1)}).scalar_one()
        print(json.dumps({'tenant': tenant, 'user': uid, 'client': client, 'lead': lead,
                          'interaction': interaction, 'customer_tenants': customer_tenants,
                          'seeded': {
                              'clients': c.execute(text('SELECT count(*) FROM public.clients WHERE tenant_id=:t'), {'t': tenant}).scalar_one(),
                              'leads': c.execute(text('SELECT count(*) FROM public.leads WHERE tenant_id=:t'), {'t': tenant}).scalar_one()}}))

    elif step == 'grant':
        tenant, uid = tenant_and_user(c)
        now = datetime.utcnow()
        ident = secretsin['grant_id']
        c.execute(text('INSERT INTO public.ai_connections (id, tenant_id, user_id, client_id, resource, scopes, '
                       'created_at, expires_at, active_until) VALUES (:id, :t, :u, :c, :r, CAST(:s AS json), :now, :exp, :exp)'),
                  {'id': ident, 't': tenant, 'u': uid, 'c': args['client'], 'r': args['resource'],
                   's': json.dumps(args['scopes']), 'now': now, 'exp': now + timedelta(hours=1)})
        c.execute(text('INSERT INTO public.oauth_credentials (token_hash, tenant_id, user_id, connection_id, kind, '
                       "scopes, created_at, expires_at) VALUES (:h, :t, :u, :id, 'access', CAST(:s AS json), :now, :exp)"),
                  {'h': secretsin['hash'], 't': tenant, 'u': uid, 'id': ident, 's': json.dumps(args['scopes']),
                   'now': now, 'exp': now + timedelta(minutes=30)})
        print(json.dumps({'grant': ident}))

    elif step == 'inspect':
        tenant, uid = tenant_and_user(c)
        print(json.dumps({
            'approved_lead': c.execute(text('SELECT count(*) FROM public.leads WHERE tenant_id=:t AND name=:n'),
                                       {'t': tenant, 'n': args['approved']}).scalar_one(),
            'cancelled_lead': c.execute(text('SELECT count(*) FROM public.leads WHERE tenant_id=:t AND name=:n'),
                                        {'t': tenant, 'n': args['cancelled']}).scalar_one(),
            'leads_total': c.execute(text('SELECT count(*) FROM public.leads WHERE tenant_id=:t'), {'t': tenant}).scalar_one(),
            'receipts': c.execute(text('SELECT count(*) FROM public.ai_write_actions WHERE tenant_id=:t'),
                                  {'t': tenant}).scalar_one(),
            'other_tenant_rows': c.execute(text('SELECT count(*) FROM public.leads WHERE tenant_id<>:t'),
                                           {'t': tenant}).scalar_one()}))

    elif step == 'revoke':
        print(json.dumps({'revoked': c.execute(text(
            'UPDATE public.ai_connections SET revoked_at=:now WHERE id = ANY(:ids) AND revoked_at IS NULL'),
            {'ids': args['grants'], 'now': datetime.utcnow()}).rowcount}))

    elif step == 'teardown':
        tenant, uid = tenant_and_user(c)
        if tenant is None:
            print(json.dumps({'removed': False})); raise SystemExit(0)
        if c.execute(text('SELECT count(*) FROM public.users WHERE tenant_id=:t'), {'t': tenant}).scalar_one() > 1:
            raise SystemExit('Refusing to remove a tenant with other users')
        for table in ('oauth_credentials', 'ai_write_actions', 'ai_tool_audits', 'ai_connections',
                      'interactions', 'contacts', 'projects', 'accounts', 'leads', 'clients',
                      'activity_logs', 'chat_messages', 'files', 'subscriptions'):
            c.execute(text(f'DELETE FROM public.{table} WHERE tenant_id=:t'), {'t': tenant})
        # Present once mobile_change_feed is applied; its delete trigger just wrote these.
        if c.execute(text("SELECT to_regclass('public.sync_purges')")).scalar() is not None:
            c.execute(text('DELETE FROM public.sync_purges WHERE tenant_id=:t'), {'t': tenant})
        c.execute(text('DELETE FROM public.user_preferences WHERE user_id=:u'), {'u': uid})
        c.execute(text('DELETE FROM public.user_roles WHERE user_id=:u'), {'u': uid})
        c.execute(text('DELETE FROM public.users WHERE id=:u'), {'u': uid})
        c.execute(text('DELETE FROM public.tenants WHERE id=:t'), {'t': tenant})
        print(json.dumps({'removed': True}))
engine.dispose()
'''

TENANT_CONFIG = {
    'branding': {'companyName': TENANT_NAME, 'primaryColor': '#2563eb', 'secondaryColor': '#64748b',
                 'logo': None, 'logoCompact': None},
    'labels': {'client': 'Client', 'lead': 'Lead', 'project': 'Project', 'interaction': 'Interaction'},
    'leads': {'statuses': ['open', 'qualified', 'proposal', 'won', 'lost'],
              'sources': ['Website', 'Referral', 'Cold Call', 'Trade Show', 'Other'],
              'temperatures': ['hot', 'warm', 'cold']},
    'businessTypes': ['None', 'Professional Services', 'Technology', 'Other'],
    'regional': {'currency': 'USD', 'currencySymbol': '$', 'dateFormat': 'MM/DD/YYYY',
                 'phoneFormat': 'US', 'addressFormat': 'US'},
    'features': {'showTemperature': True, 'showLeadScore': False, 'showSource': True,
                 'enableProjectStandalone': True, 'enableBulkOperations': True,
                 'enableAdvancedFilters': True, 'enableDataExport': True},
    'defaults': {'leadsPerPage': 10, 'clientsPerPage': 10, 'projectsPerPage': 10,
                 'defaultView': 'cards', 'defaultSort': 'newest'},
}


def remote(spec, step, secret=None):
    """Run one database step inside the target's Fly machine. Secrets go on stdin."""
    helper_path = os.getenv(spec['helper_env'], spec['helper_default'])
    if not helper_path:
        raise SystemExit(f"Set {spec['helper_env']} to the administrator URL helper script")
    helper = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', helper_path],
                            capture_output=True, check=True)
    payload = base64.b64encode(json.dumps({**step, 'target': spec}).encode()).decode()
    encoded = base64.b64encode(REMOTE.encode()).decode()
    command = (f"python -c \"import base64,sys;sys.argv=['r',base64.b64decode('{payload}').decode()];"
               f"exec(base64.b64decode('{encoded}'))\"")
    stdin = helper.stdout.rstrip(b'\r\n') + b'\n' + json.dumps(secret or {}).encode() + b'\n'
    result = subprocess.run(['flyctl', 'ssh', 'console', '--app', spec['app'], '-C', command],
                            input=stdin, capture_output=True)
    lines = [line for line in result.stdout.decode(errors='replace').splitlines() if line.startswith('{')]
    if not lines:
        sys.stderr.write(result.stderr.decode(errors='replace')[-2000:] + '\n')
        raise SystemExit(f'Remote step {step["step"]} failed')
    return json.loads(lines[-1])


BASE = ''  # Set from the chosen target in main().
OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def http_call(method, path, body=None, token=None, jwt=None, origin=False):
    headers = {'Accept': 'application/json, text/event-stream', 'MCP-Protocol-Version': '2025-11-25'}
    if body is not None:
        headers['Content-Type'] = 'application/json'
    if token:
        headers['Authorization'] = 'Bearer ' + token
    if jwt:
        headers['Authorization'] = 'Bearer ' + jwt
    if origin:
        headers['Origin'] = BASE
    request = urllib.request.Request(BASE + path, headers=headers, method=method,
                                     data=json.dumps(body).encode() if body is not None else None)
    try:
        with OPENER.open(request, timeout=45) as response:
            raw = response.read()
            return response.status, (json.loads(raw) if raw and raw[:1] in b'{[' else None), dict(response.headers)
    except urllib.error.HTTPError as error:
        raw = error.read()
        return error.code, (json.loads(raw) if raw and raw[:1] in b'{[' else None), dict(error.headers)


def rpc(token, method, params):
    status, body, _ = http_call('POST', '/mcp', {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params},
                                token=token)
    return status, body


def call_tool(token, name, arguments):
    status, body = rpc(token, 'tools/call', {'name': name, 'arguments': arguments})
    result = (body or {}).get('result') or {}
    return status, result


def text_of(result):
    return '\n'.join(part.get('text', '') for part in result.get('content') or [])


def value_of(result):
    """Tool results carry the same payload as JSON text and structured content."""
    if isinstance(result.get('structuredContent'), dict):
        return result['structuredContent']
    try:
        return json.loads(text_of(result))
    except ValueError:
        return {}


def approve(review_url, jwt, approved):
    """Review a proposal in the browser the way a signed-in user does."""
    path = urllib.parse.urlparse(review_url).path
    status, _, _ = http_call('GET', path)
    if status != 200:
        return {'page_status': status}
    status, preview, _ = http_call('POST', path + '/preview', body=None, jwt=jwt, origin=True)
    if status != 200 or not (preview or {}).get('intent'):
        return {'page_status': 200, 'preview_status': status}
    status, decision, _ = http_call('POST', path + '/decision',
                                    {'intent': preview['intent'], 'approved': approved}, jwt=jwt, origin=True)
    return {'page_status': 200, 'preview_status': 200, 'preview_state': preview.get('status'),
            'decision_status': status, 'decision': decision}


def main():
    global BASE
    parser = argparse.ArgumentParser()
    parser.add_argument('--target', choices=sorted(TARGETS), required=True,
                        help='rehearse on staging before running against production')
    parser.add_argument('--remove-tenant', action='store_true',
                        help='delete the synthetic tenant, its user and its records when finished')
    options = parser.parse_args()
    spec = TARGETS[options.target]
    BASE = spec['base']
    results, grants = {}, []
    stamp = secrets.token_hex(3)
    approved_name = f'Connector Check Approved {stamp}'
    cancelled_name = f'Connector Check Cancelled {stamp}'

    # 1. Discovery metadata.
    status, resource, _ = http_call('GET', '/.well-known/oauth-protected-resource/mcp')
    status2, server, _ = http_call('GET', '/.well-known/oauth-authorization-server')
    results['metadata_ok'] = (status == 200 and status2 == 200
                              and resource['resource'] == BASE + '/mcp'
                              and resource['authorization_servers'] == [BASE]
                              and resource['scopes_supported'] == server['scopes_supported'])
    results['published_scopes'] = len(resource['scopes_supported'])

    # 2. Unauthenticated refusal.
    status, _, headers = http_call('POST', '/mcp', {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
    results['unauthenticated'] = (status == 401
                                  and 'oauth-protected-resource/mcp' in headers.get('www-authenticate', ''))

    # 3. Synthetic tenant, ordinary user and records.
    password = secrets.token_urlsafe(24)
    setup = remote(spec, {'step': 'setup', 'tenant_name': TENANT_NAME, 'config': TENANT_CONFIG}, {'password': password})
    results['synthetic_tenant'] = setup['tenant']
    results['customer_tenants_untouched'] = setup['customer_tenants']
    results['seeded'] = setup['seeded']

    # 4. Self-registration, then one write-capable and one read-only grant.
    status, client, _ = http_call('POST', '/oauth/register',
                                  {'client_name': CLIENT_NAME, 'redirect_uris': ['http://127.0.0.1/callback']})
    results['self_registration'] = status == 201
    client_id = client['client_id']
    tokens = {}
    for label, scopes in (('write', WRITE_SCOPES), ('read', READ_SCOPES)):
        raw = secrets.token_urlsafe(32)
        grant = remote(spec, {'step': 'grant', 'client': client_id, 'resource': BASE + '/mcp', 'scopes': scopes},
                       {'hash': hashlib.sha256(raw.encode('ascii')).hexdigest(), 'grant_id': str(uuid.uuid4())})
        tokens[label] = raw
        grants.append(grant['grant'])

    # 5. Discovery and reads.
    status, listing = rpc(tokens['write'], 'tools/list', {})
    names = sorted(tool['name'] for tool in (listing or {}).get('result', {}).get('tools', []))
    results['tools_listed'] = names
    results['decide_tool_withheld'] = not any(name.startswith('decide_') for name in names)
    status, clients_read = call_tool(tokens['write'], 'list_clients', {'limit': 5})
    results['read_allowed'] = status == 200 and not clients_read.get('isError')
    results['read_sees_synthetic_client'] = 'Verification Client Co.' in text_of(clients_read)
    today = datetime.now().date()  # The operator workstation runs in the CRM's local zone.
    status, schedule = call_tool(tokens['write'], 'get_schedule', {
        'start_date': today.isoformat(), 'end_date': (today + timedelta(days=7)).isoformat()})
    results['schedule_read'] = status == 200 and not schedule.get('isError')
    results['schedule_sees_synthetic_follow_up'] = 'Verification check-in' in text_of(schedule)

    # 6. A read-only grant cannot write and is told what to request.
    status, refused = call_tool(tokens['read'], 'prepare_lead_creation', {
        'request_key': secrets.token_urlsafe(16), 'lead': {'name': 'MUST-NOT-BE-CREATED ' + stamp}})
    challenge = (refused.get('_meta') or {}).get('mcp/www_authenticate', [''])[0]
    results['write_refused_for_read_only'] = refused.get('isError') is True
    results['refusal_names_scope'] = 'insufficient_scope' in challenge and 'leads:create' in challenge

    # 7. Propose, review in the browser, approve; then propose and cancel.
    status, login, _ = http_call('POST', '/api/login', {'email': USER_EMAIL, 'password': password})
    results['synthetic_user_login'] = status == 200
    jwt = (login or {}).get('token')
    for label, name, decision in (('approved', approved_name, True), ('cancelled', cancelled_name, False)):
        status, proposal = call_tool(tokens['write'], 'prepare_lead_creation', {
            'request_key': secrets.token_urlsafe(16),
            'lead': {'name': name, 'contact_person': 'Reviewer Test', 'email': f'{label}@verification.example.com',
                     'phone': '+15125550199', 'city': 'Austin', 'state': 'TX'}})
        receipt = value_of(proposal)
        review_url, action_id = receipt.get('review_url'), receipt.get('action_id')
        results[f'{label}_proposed'] = (status == 200 and not proposal.get('isError')
                                        and receipt.get('status') == 'pending')
        results[f'{label}_has_review_link'] = bool(review_url)
        results[f'{label}_card_withheld'] = proposal.get('_meta') is None
        if proposal.get('isError'):
            results[f'{label}_error'] = text_of(proposal)[:300]
        if review_url and jwt:
            results[f'{label}_review'] = approve(review_url, jwt, decision)
        if action_id:
            status, state = call_tool(tokens['write'], 'get_lead_creation', {'action_id': action_id})
            reported = value_of(state).get('status')
            results[f'{label}_reported_status'] = reported
            if label == 'approved':
                results['receipt_reports_saved'] = status == 200 and reported == 'committed'

    # 8. Database outcomes and tenant isolation.
    inspect = remote(spec, {'step': 'inspect', 'approved': approved_name, 'cancelled': cancelled_name})
    results['approved_lead_saved_once'] = inspect['approved_lead'] == 1
    results['cancelled_lead_absent'] = inspect['cancelled_lead'] == 0
    results['synthetic_tenant_leads'] = inspect['leads_total']
    results['customer_leads_present_and_unseen'] = inspect['other_tenant_rows'] > 0
    # Isolation: the connection returned every client it can see, and they are
    # exactly the synthetic ones, although other tenants hold many more records.
    seen = {entry.get('name') for entry in (value_of(clients_read).get('clients') or [])}
    results['clients_visible_to_connection'] = sorted(name for name in seen if name)
    results['read_did_not_leak_other_tenants'] = seen == {'Verification Client Co.'}

    # 9. Revocation refuses everything afterwards.
    results['revoked'] = remote(spec, {'step': 'revoke', 'grants': grants})['revoked'] == len(grants)
    results['after_revoke_list'] = rpc(tokens['write'], 'tools/list', {})[0]
    results['after_revoke_call'] = call_tool(tokens['write'], 'list_clients', {})[0]

    if options.remove_tenant:
        # Cleanup must never hide the check's own result.
        try:
            results['tenant_removed'] = remote(spec, {'step': 'teardown'})['removed']
        except SystemExit as error:
            results['tenant_removed'] = f'failed: {error}'

    checks = [results['metadata_ok'], results['unauthenticated'], results['self_registration'],
              results['read_allowed'], results['read_sees_synthetic_client'], results['schedule_read'],
              results['decide_tool_withheld'], results['write_refused_for_read_only'],
              results['refusal_names_scope'], results['synthetic_user_login'],
              results['approved_proposed'], results['approved_has_review_link'],
              results.get('approved_review', {}).get('decision_status') == 200,
              results.get('cancelled_review', {}).get('decision_status') == 200,
              results['approved_lead_saved_once'], results['cancelled_lead_absent'],
              results['read_did_not_leak_other_tenants'], results['revoked'],
              results['after_revoke_list'] == 401, results['after_revoke_call'] == 401]
    passed = all(checks)
    print(json.dumps({'target': options.target, 'base': BASE, 'client': client_id, 'grants': grants,
                      'passed': passed, **results}, indent=1, default=str))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
