import asyncio
import html
import re
import secrets
import time
from datetime import datetime, timedelta
from urllib.parse import urlencode, urlsplit, parse_qs
import pytest
from authlib.jose import jwt
from authlib.oauth2.rfc6749.errors import InvalidGrantError, InvalidScopeError
from authlib.oauth2.rfc7636.challenge import create_s256_code_challenge
from app.models import AIClient, AIConnection, OAuthCredential, User, Tenant
from app.services.oauth import digest, lookup_principal, validate_access
from app.services.principal import Principal
from app.utils import auth_utils
from app.utils.rate_limiter import reset_rate_limit
from test_security_boundaries import crm

BASE = 'https://example.test'
RESOURCE = BASE+'/mcp'
REDIRECT = 'https://client.test/callback'


@pytest.fixture
def oauth(crm, monkeypatch):
    monkeypatch.setenv('MCP_RESOURCE_URI',RESOURCE)
    reset_rate_limit()
    with crm[1]() as db:
        db.add(AIClient(id='pilot',name='Pilot <AI>',is_active=True,oauth_enabled=True,
            allowed_scopes=['clients:read','reports:read'],redirect_uris=[REDIRECT]))
        db.commit()
    return Flow(crm)


class Flow:
    def __init__(self, crm):
        self.call,self.admin,self.app = crm
        self.app.config['SERVER_NAME']='example.test'
        self.client = self.app.test_client()
        self.verifier = secrets.token_urlsafe(32)
        self.params = dict(client_id='pilot',response_type='code',redirect_uri=REDIRECT,
            scope='clients:read',state='client-state',resource=RESOURCE,
            code_challenge=create_s256_code_challenge(self.verifier),code_challenge_method='S256')
    def request(self,method,path,**kwargs):
        async def run():
            response = await self.client.open(path,method=method,scheme='https',**kwargs)
            body = await response.get_data(as_text=True)
            return response.status_code,body,response.headers
        return asyncio.run(run())
    def bearer(self,user=1):
        return 'Bearer '+jwt.encode({'alg':'HS256'},{'sub':user,'exp':int(time.time())+300},self.app.config['SECRET_KEY']).decode()
    def begin(self):
        status,body,headers = self.request('GET','/oauth/authorize?'+urlencode(self.params))
        assert status==200,body
        assert 'Pilot &lt;AI&gt;' in body
        assert "frame-ancestors 'none'" in headers['Content-Security-Policy']
        assert 'HttpOnly' in headers['Set-Cookie'] and 'Secure' in headers['Set-Cookie']
        self.intent = html.unescape(re.search(r'data-intent="([^"]+)"',body)[1])
    def decision(self,approved=True,user=1,origin=BASE,intent=None):
        return self.request('POST','/oauth/decision',json={'intent':intent or self.intent,'approved':approved},
            headers={'Origin':origin,'Authorization':self.bearer(user)})
    def code(self):
        self.begin()
        status,body,_ = self.decision()
        assert status==200,body
        import json
        params = parse_qs(urlsplit(json.loads(body)['redirect']).query)
        assert params['state']==['client-state'] and params['iss']==[BASE]
        return params['code'][0]
    def exchange(self,code,**changes):
        data=dict(grant_type='authorization_code',client_id='pilot',code=code,code_verifier=self.verifier,
            redirect_uri=REDIRECT,resource=RESOURCE)
        data.update(changes)
        return self.form('/oauth/token',data)
    def form(self,path,data):
        import json
        status,body,headers=self.request('POST',path,form=data)
        assert headers['Cache-Control']=='no-store'
        return status,json.loads(body)
    def refresh(self,raw,**changes):
        return self.form('/oauth/token',dict(grant_type='refresh_token',client_id='pilot',resource=RESOURCE,refresh_token=raw,**changes))
    def tokens(self):
        status,body = self.exchange(self.code())
        assert status==200,body
        return body
    def access(self,raw,scopes=None,resource=RESOURCE):
        principal=lookup_principal(auth_utils.SessionLocal,raw)
        with auth_utils.SessionLocal() as db:
            return validate_access(db,principal,raw,resource,scopes or ['clients:read'])


def test_browser_code_exchange_rotation_and_replay_revokes_family(oauth,caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    token=oauth.tokens()
    assert token['expires_in']<=300 and token['resource']==RESOURCE
    identity=oauth.access(token['access_token'])
    assert identity.user_id==1 and identity.tenant_id==1 and identity.scopes==frozenset({'clients:read'})
    with pytest.raises(TypeError):
        from app.services.base import TenantService
        with auth_utils.SessionLocal() as db: TenantService(db,identity)
    status,new=oauth.refresh(token['refresh_token'])
    assert status==200 and new['refresh_token']!=token['refresh_token']
    assert oauth.access(new['access_token'])
    assert oauth.refresh(token['refresh_token'])[0]==400
    with pytest.raises((InvalidGrantError,PermissionError)): oauth.access(new['access_token'])
    assert oauth.refresh(new['refresh_token'])[0]==400
    with oauth.admin() as db:
        rows=db.query(OAuthCredential).all()
        assert len(rows)==5 and all(len(row.token_hash)==64 for row in rows)
        assert all(row.token_hash not in (token['access_token'],token['refresh_token']) for row in rows)
        assert db.query(AIConnection).one().revoked_at is not None
    assert token['access_token'] not in caplog.text and token['refresh_token'] not in caplog.text


@pytest.mark.parametrize('change',[
    {'redirect_uri':'https://evil.test/callback'}, {'code_challenge_method':'plain'},
    {'code_challenge':''}, {'resource':'https://wrong.test/mcp'}, {'scope':'clients:write'},
    {'response_type':'token'},{'client_id':'missing'},{'state':''}, {'scope':'clients:read clients:read'}])
def test_authorization_rejects_unregistered_or_unsafe_requests(oauth,change):
    oauth.params.update(change)
    status,body,headers=oauth.request('GET','/oauth/authorize?'+urlencode(oauth.params))
    assert status in (400,401) and 'Location' not in headers
    with oauth.admin() as db: assert db.query(AIConnection).count()==0


def test_duplicate_parameters_and_json_tokens_are_rejected(oauth):
    assert oauth.request('GET','/oauth/authorize?'+urlencode(oauth.params)+'&client_id=evil')[0]==400
    assert oauth.request('POST','/oauth/token',json={'grant_type':'authorization_code'})[0]==400
    assert oauth.request('POST','/oauth/token',data='client_id=pilot&client_id=evil',headers={'Content-Type':'application/x-www-form-urlencoded'})[0]==400


@pytest.mark.parametrize('locales',['en-US','fr-CA fr en','zz-ZZ'])
def test_ui_locales_hint_allows_consent_and_token_exchange(oauth,locales):
    oauth.params['ui_locales']=locales
    tokens=oauth.tokens()
    identity=oauth.access(tokens['access_token'])
    assert identity.scopes==frozenset({'clients:read'})


def test_duplicate_ui_locales_are_rejected(oauth):
    status,_,headers=oauth.request('GET','/oauth/authorize?'+urlencode(oauth.params)+'&ui_locales=en-US&ui_locales=fr')
    assert status==400 and 'Location' not in headers
    with oauth.admin() as db:
        assert db.query(AIConnection).count()==db.query(OAuthCredential).count()==0


@pytest.mark.parametrize('change',[
    {'redirect_uri':'https://evil.test/callback'}, {'scope':'clients:write'},
    {'code_challenge_method':'plain'}, {'resource':None}, {'unexpected':'value'}])
def test_ui_locales_does_not_relax_authorization_checks(oauth,change):
    oauth.params.update(ui_locales='en-US',**change)
    params={key:value for key,value in oauth.params.items() if value is not None}
    status,_,headers=oauth.request('GET','/oauth/authorize?'+urlencode(params))
    assert status==400 and 'Location' not in headers
    with oauth.admin() as db:
        assert db.query(AIConnection).count()==db.query(OAuthCredential).count()==0


@pytest.mark.parametrize('case',['origin','intent','cookie','approval','denied'])
def test_consent_requires_browser_binding_and_explicit_decision(oauth,case):
    oauth.begin()
    if case=='cookie': oauth.client=oauth.app.test_client()
    result=oauth.decision(origin='https://evil.test' if case=='origin' else BASE,
        intent=oauth.intent+'x' if case=='intent' else None,
        approved=False if case=='denied' else 1 if case=='approval' else True)
    if case=='denied': assert result[0]==200 and 'access_denied' in result[1]
    else: assert result[0]==400
    with oauth.admin() as db:
        assert db.query(AIConnection).count()==db.query(OAuthCredential).count()==0


@pytest.mark.parametrize('change',[{'code_verifier':'x'*43},{'redirect_uri':'https://evil.test/callback'},
    {'resource':'https://evil.test/mcp'},{'client_id':'missing'}])
def test_wrong_pkce_client_redirect_or_resource_never_consumes_code(oauth,change):
    code=oauth.code()
    assert oauth.exchange(code,**change)[0] in (400,401)
    assert oauth.exchange(code)[0]==200


def test_code_replay_revokes_existing_tokens(oauth):
    code=oauth.code()
    status,token=oauth.exchange(code)
    assert status==200
    assert oauth.exchange(code)[0]==400
    with pytest.raises((InvalidGrantError,PermissionError)): oauth.access(token['access_token'])


@pytest.mark.parametrize('change',['user','tenant','client','oauth','scope','role','revoked'])
def test_current_permissions_checked_on_access_and_refresh(oauth,change):
    if change=='role': oauth.params['scope']='reports:read'
    token=oauth.tokens()
    with oauth.admin() as db:
        if change=='user': db.get(User,1).is_active=False
        elif change=='tenant': db.get(Tenant,1).is_active=False
        elif change=='client': db.get(AIClient,'pilot').is_active=False
        elif change=='oauth': db.get(AIClient,'pilot').oauth_enabled=False
        elif change=='scope': db.get(AIClient,'pilot').allowed_scopes=['reports:read']
        elif change=='role': db.get(User,1).roles=[]
        else: db.query(AIConnection).one().revoked_at=datetime.utcnow()
        db.commit()
    with pytest.raises((InvalidGrantError,PermissionError,LookupError)):
        oauth.access(token['access_token'],[oauth.params['scope']])
    assert oauth.refresh(token['refresh_token'])[0] in (400,401)


def test_revoke_is_bound_to_client_and_web_tokens_are_not_delegated(oauth):
    token=oauth.tokens()
    assert oauth.form('/oauth/revoke',{'client_id':'wrong','token':token['access_token']})[0]==200
    assert oauth.access(token['access_token'])
    assert oauth.request('GET','/api/ai-connections',headers={'Authorization':'Bearer '+token['access_token']})[0]==401
    assert lookup_principal(auth_utils.SessionLocal,oauth.bearer()[7:]) is None
    for raw in (token['access_token'],token['refresh_token'],'invalid'):
        assert oauth.form('/oauth/revoke',{'client_id':'pilot','token':raw})[0]==200
    with pytest.raises((InvalidGrantError,PermissionError)): oauth.access(token['access_token'])


def test_scope_and_owner_cannot_expand(oauth):
    token=oauth.tokens()
    with pytest.raises(InvalidScopeError): oauth.access(token['access_token'],['reports:read'])
    assert oauth.refresh(token['refresh_token'],scope='reports:read')[0]==400
    for principal in (Principal(2,2,frozenset({'admin'})),Principal(3,1,frozenset())):
        with auth_utils.SessionLocal() as db:
            with pytest.raises(InvalidGrantError): validate_access(db,principal,token['access_token'],RESOURCE,['clients:read'])
    assert oauth.access(token['access_token'])


def test_discovery_and_errors_have_no_cache(oauth):
    status,body,headers=oauth.request('GET','/.well-known/oauth-authorization-server')
    assert status==200 and 'S256' in body and 'registration_endpoint' not in body
    assert headers['Cache-Control']=='no-store'
    assert oauth.request('POST','/oauth/decision',json={})[2]['Cache-Control']=='no-store'


@pytest.mark.parametrize('kind',['code','access','refresh'])
def test_expired_credentials_fail_closed(oauth,monkeypatch,kind):
    from app.services import oauth as implementation
    code=oauth.code()
    if kind!='code':
        status,tokens=oauth.exchange(code)
        assert status==200
    class Future(datetime):
        @classmethod
        def utcnow(cls): return datetime.utcnow()+timedelta(days=8)
    monkeypatch.setattr(implementation,'datetime',Future)
    if kind=='code': assert oauth.exchange(code)[0]==400
    elif kind=='refresh': assert oauth.refresh(tokens['refresh_token'])[0]==400
    else:
        with pytest.raises(InvalidGrantError): oauth.access(tokens['access_token'])


def test_expired_intent_and_role_loss_prevent_approval(oauth,monkeypatch):
    oauth.params['scope']='reports:read'
    oauth.begin()
    with oauth.admin() as db:
        db.get(User,1).roles=[]
        db.commit()
    assert oauth.decision()[0]==403
    import itsdangerous.timed
    original=itsdangerous.timed.TimestampSigner.get_timestamp
    monkeypatch.setattr(itsdangerous.timed.TimestampSigner,'get_timestamp',lambda self:original(self)+301)
    assert oauth.decision()[0]==400
    with oauth.admin() as db: assert db.query(AIConnection).count()==0


def test_consent_intent_cannot_create_a_second_grant(oauth):
    oauth.begin()
    cookies=list(oauth.client.cookie_jar)
    assert oauth.decision()[0]==200
    for cookie in cookies: oauth.client.cookie_jar.set_cookie(cookie)
    assert oauth.decision()[0]==400
    with oauth.admin() as db: assert db.query(AIConnection).count()==1


def test_refresh_can_reduce_but_never_restore_scope(oauth):
    oauth.params['scope']='clients:read reports:read'
    token=oauth.tokens()
    status,narrow=oauth.refresh(token['refresh_token'],scope='clients:read')
    assert status==200 and narrow['scope']=='clients:read'
    assert oauth.refresh(narrow['refresh_token'],scope='clients:read reports:read')[0]==400
    assert oauth.refresh(narrow['refresh_token'])[0]==200


def test_parallel_code_exchanges_issue_once_and_revoke_on_replay(oauth):
    if oauth.admin.kw['bind'].dialect.name!='postgresql': pytest.skip('Requires PostgreSQL row locks')
    from concurrent.futures import ThreadPoolExecutor
    code=oauth.code()
    def exchange(_):
        flow=Flow((oauth.call,oauth.admin,oauth.app))
        flow.verifier=oauth.verifier
        return flow.exchange(code)
    with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(exchange,range(2)))
    assert sorted(status for status,_ in results)==[200,400]
    token=next(body for status,body in results if status==200)
    with pytest.raises(InvalidGrantError): oauth.access(token['access_token'])


def test_credential_owner_rls_and_immutable_history(oauth):
    if oauth.admin.kw['bind'].dialect.name!='postgresql': pytest.skip('Requires PostgreSQL row policies')
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    from app.services.database_context import bind_principal
    token=oauth.tokens()
    for principal in (None,Principal(2,2,frozenset({'admin'})),Principal(3,1,frozenset())):
        with auth_utils.SessionLocal() as db:
            if principal: bind_principal(db,principal)
            assert db.execute(text('SELECT token_hash FROM oauth_credentials')).all()==[]
    with auth_utils.SessionLocal() as db:
        bind_principal(db,Principal(1,1,frozenset({'admin'})))
        assert db.query(OAuthCredential).count()==3
        for statement in ("UPDATE oauth_credentials SET scopes='[\"reports:read\"]'",
            'UPDATE oauth_credentials SET expires_at=expires_at+interval \'1 day\'',
            'UPDATE oauth_credentials SET user_id=3', 'DELETE FROM oauth_credentials',
            'UPDATE oauth_credentials SET used_at=NULL WHERE used_at IS NOT NULL'):
            with db.begin_nested() as sp:
                with pytest.raises(DBAPIError): db.execute(text(statement))
                sp.rollback()
    # After returning pooled connections, an anonymous session sees nothing.
    with auth_utils.SessionLocal() as db:
        assert db.query(OAuthCredential).count()==0
