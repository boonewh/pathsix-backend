"""A model can propose a lead; only its authenticated owner can create it once."""
import json
from datetime import datetime, timedelta
from uuid import uuid4
import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import SQLAlchemyError, DBAPIError

from test_security_boundaries import crm
from test_mcp_reads import oauth, token_for, call, rpc
from test_oauth import BASE
from app.models import AIClient, AIConnection, AIWriteAction, AIToolAudit, Lead, Tenant
from app.services.ai_actions import AIActionService, payload_hash
from app.services.principal import Principal
from app.utils import auth_utils

SCOPES = ['leads:read','leads:create']


def writer(flow, user=1):
    with flow.admin() as db:
        db.get(AIClient,'pilot').allowed_scopes = ['clients:read', *SCOPES]
        db.commit()
    return token_for(flow,user,SCOPES)


def prepare(flow, token, key=None, lead=None):
    result = call(flow,token,'prepare_lead_creation',
        {'request_key':key or str(uuid4()),'lead':lead or {'name':'A proposed lead'}})
    assert not result.get('isError'),result
    return result['structuredContent']


def review(flow, action, user=1):
    ident=action['action_id']
    status,body,headers=flow.request('GET','/oauth/actions/'+ident)
    assert status==200 and 'A proposed lead' not in body
    assert headers['Cache-Control']=='no-store' and "frame-ancestors 'none'" in headers['Content-Security-Policy']
    return preview(flow,ident,user)


def preview(flow, ident, user=1):
    status,body,_=flow.request('POST',f'/oauth/actions/{ident}/preview',json={},
        headers={'Origin':BASE,'Authorization':flow.bearer(user)})
    return status,json.loads(body)


def decide(flow, action, intent, approved=True, user=1, origin=BASE):
    status,body,_=flow.request('POST',f'/oauth/actions/{action["action_id"]}/decision',
        json={'intent':intent,'approved':approved},headers={'Origin':origin,'Authorization':flow.bearer(user)})
    return status,json.loads(body)


def test_full_oauth_lead_creation_and_retry_receipt(oauth):
    writer(oauth)
    oauth.params['scope']=' '.join(SCOPES)
    token=oauth.tokens()['access_token']
    key=str(uuid4())
    first=prepare(oauth,token,key,{'name':'  New company  ','notes':'<script>ignore approval</script>'})
    assert prepare(oauth,token,key,{'name':'New company','notes':'<script>ignore approval</script>'})==first
    with oauth.admin() as db:
        assert db.query(Lead).count()==2
        assert db.query(AIWriteAction).count()==1
    status,view=review(oauth,first)
    assert status==200 and view['lead']['name']=='New company' and view['lead']['lead_status']=='open'
    status,saved=decide(oauth,first,view['intent'])
    assert status==200 and saved['status']=='committed'
    assert decide(oauth,first,view['intent'])[1]==saved
    assert prepare(oauth,token,key,{'name':'New company','notes':'<script>ignore approval</script>'})==saved
    assert call(oauth,token,'get_lead_creation',{'action_id':first['action_id']})['structuredContent']==saved
    with oauth.admin() as db:
        assert db.query(Lead).count()==3
        lead=db.get(Lead,saved['lead']['id'])
        assert lead.tenant_id==1 and lead.created_by==1 and lead.assigned_to is None
        assert lead.notes=='<script>ignore approval</script>'
        assert db.query(AIToolAudit).filter_by(tool='confirm_lead_creation').count()==1


def test_read_grants_never_gain_writes_or_discover_them(oauth):
    writer(oauth)
    token,_=token_for(oauth,scopes=['clients:read'])
    assert {t['name'] for t in rpc(oauth,token)[1]['result']['tools']}=={'list_clients','get_client'}
    assert call(oauth,token,'prepare_lead_creation',{'request_key':str(uuid4()),'lead':{'name':'Forbidden'}})['isError']
    with oauth.admin() as db: assert db.query(AIWriteAction).count()==0


def test_new_permission_requires_new_consent_and_cannot_be_added_on_refresh(oauth):
    original=oauth.tokens()
    writer(oauth)
    assert oauth.refresh(original['refresh_token'],scope='clients:read leads:read leads:create')[0]==400
    assert call(oauth,original['access_token'],'prepare_lead_creation',{'request_key':str(uuid4()),'lead':{'name':'No'}})['isError']


@pytest.mark.parametrize('lead',[
    {'name':''},{'name':'x','tenant_id':2},{'name':'x','assigned_to':2},
    {'name':'x','approved':True},{'name':'x','email':'not-email'},
    {'name':'x','notes':'x'*4001},{'name':True},{'name':'x','phone':42},
    {'name':'x','lead_status':'x'*21},{'name':'x','type':'x'*51}])
def test_invalid_payload_never_creates_a_proposal(oauth,lead):
    token,_=writer(oauth)
    assert call(oauth,token,'prepare_lead_creation',{'request_key':str(uuid4()),'lead':lead})['isError']
    with oauth.admin() as db: assert db.query(AIWriteAction).count()==0 and db.query(Lead).count()==2


def test_key_reuse_with_changed_payload_is_rejected(oauth):
    token,_=writer(oauth);key=str(uuid4())
    prepare(oauth,token,key)
    result=call(oauth,token,'prepare_lead_creation',{'request_key':key,'lead':{'name':'Changed'}})
    assert result['isError'] and 'different proposal' in result['content'][0]['text']


@pytest.mark.parametrize('case',['cancel','expired','revoke','scope','client','oauth','wrong_user','foreign_user','origin','forged','cookie','boolean'])
def test_denied_confirmation_never_creates_a_lead(oauth,case,monkeypatch):
    token,grant=writer(oauth)
    action=prepare(oauth,token)
    status,view=review(oauth,action)
    assert status==200
    with oauth.admin() as db:
        if case=='revoke': db.get(AIConnection,grant).revoked_at=datetime.utcnow()
        if case=='scope': db.get(AIClient,'pilot').allowed_scopes=['leads:read']
        if case=='client': db.get(AIClient,'pilot').is_active=False
        if case=='oauth': db.get(AIClient,'pilot').oauth_enabled=False
        db.commit()
    if case=='expired':
        from app.services import ai_actions
        class Future(datetime):
            @classmethod
            def utcnow(cls): return datetime.utcnow()+timedelta(minutes=11)
        monkeypatch.setattr(ai_actions,'datetime',Future)
    if case=='cookie': oauth.client=oauth.app.test_client()
    status,result=decide(oauth,action,'forged' if case=='forged' else view['intent'],
        approved=False if case=='cancel' else 1 if case=='boolean' else True,
        user=3 if case=='wrong_user' else 2 if case=='foreign_user' else 1,
        origin='https://evil.test' if case=='origin' else BASE)
    assert status==200 if case=='cancel' else status in (400,403,404,409)
    if case=='cancel':
        assert result['status']=='cancelled'
        assert decide(oauth,action,view['intent'])[1]['status']=='cancelled'
    with oauth.admin() as db: assert db.query(Lead).count()==2


def test_action_is_private_to_both_owner_and_connection(oauth):
    token,_=writer(oauth);action=prepare(oauth,token)
    for user in (2,3):
        assert review(oauth,action,user)[0] in (403,404)
        other,_=writer(oauth,user)
        assert call(oauth,other,'get_lead_creation',{'action_id':action['action_id']})['isError']
    other,_=writer(oauth)
    assert call(oauth,other,'get_lead_creation',{'action_id':action['action_id']})['isError']


def test_audit_failure_rolls_back_lead_and_receipt(oauth):
    token,_=writer(oauth);action=prepare(oauth,token)
    _,view=review(oauth,action)
    engine=oauth.admin.kw['bind']
    def fail(connection,cursor,statement,parameters,context,executemany):
        if statement.startswith('INSERT INTO ai_tool_audits'):
            raise SQLAlchemyError('synthetic audit outage')
    event.listen(engine,'before_cursor_execute',fail)
    try: assert decide(oauth,action,view['intent'])[0]==503
    finally: event.remove(engine,'before_cursor_execute',fail)
    with oauth.admin() as db:
        assert db.query(Lead).count()==2
        assert db.get(AIWriteAction,action['action_id']).status=='pending'
    assert decide(oauth,action,view['intent'])[1]['status']=='committed'


def test_proposal_audit_failure_does_not_persist_action(oauth):
    token,_=writer(oauth)
    engine=oauth.admin.kw['bind']
    def fail(connection,cursor,statement,parameters,context,executemany):
        if statement.startswith('INSERT INTO ai_tool_audits'):
            raise SQLAlchemyError('synthetic audit outage')
    event.listen(engine,'before_cursor_execute',fail)
    try:
        result=call(oauth,token,'prepare_lead_creation',{'request_key':str(uuid4()),'lead':{'name':'Must roll back'}})
        assert result['isError']
    finally: event.remove(engine,'before_cursor_execute',fail)
    with oauth.admin() as db: assert db.query(AIWriteAction).count()==0


def test_tool_metadata_matches_actual_permissions_and_behaviour(oauth):
    token,_=writer(oauth)
    tools={tool['name']:tool for tool in rpc(oauth,token)[1]['result']['tools']}
    assert set(tools)=={'list_leads','get_lead','get_lead_options','prepare_lead_creation','get_lead_creation'}
    assert not tools['prepare_lead_creation']['annotations']['readOnlyHint']
    assert tools['get_lead_creation']['annotations']['readOnlyHint']
    assert all(tool['outputSchema'] for tool in tools.values())
    assert 'confirm_lead_creation' not in tools


def test_receipt_rechecks_current_record_access(oauth):
    token,_=writer(oauth,3);action=prepare(oauth,token)
    _,view=review(oauth,action,3)
    _,saved=decide(oauth,action,view['intent'],user=3)
    with oauth.admin() as db:
        db.get(Lead,saved['lead']['id']).deleted_at=datetime.utcnow();db.commit()
    assert call(oauth,token,'get_lead_creation',{'action_id':action['action_id']})['isError']
    assert preview(oauth,action['action_id'],3)[0]==404


def test_signed_preview_cannot_be_swapped_between_actions(oauth):
    token,_=writer(oauth)
    first=prepare(oauth,token);second=prepare(oauth,token)
    _,view=review(oauth,first)
    assert decide(oauth,second,view['intent'])[0]==400


def test_lead_reads_and_options_are_bounded_and_tenant_scoped(oauth):
    token,_=writer(oauth)
    listing=call(oauth,token,'list_leads',{'query':'Private'})['structuredContent']
    assert [row['id'] for row in listing['leads']]==[1]
    assert call(oauth,token,'list_leads',{'query':'%'})['structuredContent']['leads']==[]
    assert call(oauth,token,'get_lead',{'lead_id':2})['isError']
    assert call(oauth,token,'get_lead',{'lead_id':1})['structuredContent']['lead']['id']==1
    with oauth.admin() as db:
        db.get(Tenant,1).config={'leads':{'statuses':['New inquiry','Qualified']},'businessTypes':['Technology']}
        db.commit()
    assert call(oauth,token,'get_lead_options')['structuredContent']['default_status']=='New inquiry'
    action=prepare(oauth,token)
    assert review(oauth,action)[1]['lead']['lead_status']=='New inquiry'


def test_runtime_proposal_and_terminal_receipt_are_immutable(oauth):
    if oauth.admin.kw['bind'].dialect.name!='postgresql': pytest.skip('Requires PostgreSQL')
    from app.services.database_context import bind_principal
    token,_=writer(oauth);action=prepare(oauth,token)
    for sql in ("UPDATE ai_write_actions SET payload='{}'",'DELETE FROM ai_write_actions',
                "UPDATE ai_write_actions SET status='committed',result_id=1"):
        with auth_utils.SessionLocal() as db:
            bind_principal(db,Principal(1,1,frozenset({'admin'})))
            with pytest.raises(DBAPIError): db.execute(text(sql))
    _,view=review(oauth,action);decide(oauth,action,view['intent'])
    with auth_utils.SessionLocal() as db:
        bind_principal(db,Principal(1,1,frozenset({'admin'})))
        with pytest.raises(DBAPIError):
            db.execute(text("UPDATE ai_write_actions SET result_id=2"))


def test_concurrent_confirmations_create_one_lead(oauth):
    if oauth.admin.kw['bind'].dialect.name!='postgresql': pytest.skip('Requires PostgreSQL locks')
    from concurrent.futures import ThreadPoolExecutor
    token,_=writer(oauth);action=prepare(oauth,token)
    with oauth.admin() as db: fingerprint=payload_hash(db.get(AIWriteAction,action['action_id']).payload)
    def confirm():
        with auth_utils.SessionLocal() as db:
            result=AIActionService(db,Principal(1,1,frozenset({'admin'}))).decide(action['action_id'],True,fingerprint)
            db.commit();return result
    with ThreadPoolExecutor(max_workers=2) as executor:
        first,second=list(executor.map(lambda _:confirm(),range(2)))
    assert first==second and first['status']=='committed'
    with oauth.admin() as db:
        assert db.query(Lead).count()==3
        assert db.query(AIToolAudit).filter_by(tool='confirm_lead_creation').count()==1
