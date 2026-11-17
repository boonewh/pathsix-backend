"""Current identity, configuration and password ownership outside HTTP and under RLS."""
import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Event

import pytest
from authlib.jose import jwt
from sqlalchemy.exc import OperationalError

from test_security_boundaries import crm
from app.models import Tenant, User
from app.routes import auth
from app.services.identity import IdentityService
from app.services.principal import Principal
from app.utils import auth_utils

ADMIN = Principal(1,1,frozenset({'admin'}))
ORDINARY = Principal(3,1,frozenset())


@pytest.mark.parametrize('restricted', [False, True])
def test_identity_and_config_are_personal_and_detached(crm, restricted):
    _, factory, _ = crm
    with factory() as db:
        db.get(Tenant,1).config={'branding':{'name':'A'}}
        db.get(Tenant,2).config={'branding':{'name':'PRIVATE B'}}
        db.commit()
    runtime=auth_utils.SessionLocal if restricted else factory
    for principal in (ADMIN,ORDINARY):
        with runtime() as db:
            service=IdentityService(db,principal)
            me=service.me()
            assert me['id']==principal.user_id and me['tenant_id']==1
            assert me['tenant']==service.tenant_config()
            assert me['roles']==(['admin'] if principal.is_admin else [])
            assert 'password_hash' not in json.dumps(me) and 'PRIVATE B' not in json.dumps(me)
            me['tenant']['config']['branding']['name']='changed'
            assert service.tenant_config()['config']['branding']['name']=='A'
    with runtime() as db:
        with pytest.raises(TypeError):
            IdentityService(db,None)
    with runtime() as db:
        with pytest.raises(PermissionError):
            IdentityService(db,Principal(2,1,frozenset({'admin'}))).me()


@pytest.mark.parametrize('disabled', ['user','tenant'])
def test_direct_identity_denies_disabled_actor_or_tenant(crm, disabled):
    _, factory, _ = crm
    with factory() as db:
        db.get(User if disabled=='user' else Tenant,1).is_active=False
        db.commit()
    for method in ('me','tenant_config'):
        with auth_utils.SessionLocal() as db:
            with pytest.raises(PermissionError):
                getattr(IdentityService(db,ADMIN),method)()
    with auth_utils.SessionLocal() as db:
        with pytest.raises(PermissionError):
            IdentityService(db,ADMIN).change_password({'current_password':'old','new_password':'new'})


@pytest.mark.parametrize('restricted', [False, True])
def test_password_change_is_owned_and_caller_controls_durability(crm, restricted):
    _, factory, _ = crm
    original=auth_utils.hash_password('old-password')
    with factory() as db:
        db.get(User,3).password_hash=original
        foreign=db.get(User,2).password_hash
        db.commit()
    runtime=auth_utils.SessionLocal if restricted else factory
    with runtime() as db:
        service=IdentityService(db,ORDINARY)
        result=service.change_password({'current_password':'old-password','new_password':'new-password',
                                        'user_id':2,'tenant_id':2,'email':'b@example.test'})
        assert result=={'message':'Password changed successfully'}
        assert auth_utils.verify_password('new-password',db.get(User,3).password_hash)
        db.rollback()
    with factory() as db:
        assert db.get(User,3).password_hash==original and db.get(User,2).password_hash==foreign
    with runtime() as db:
        IdentityService(db,ORDINARY).change_password({'current_password':'old-password','new_password':'new-password'})
        db.commit()
    with factory() as db:
        assert auth_utils.verify_password('new-password',db.get(User,3).password_hash)
        assert db.get(User,2).password_hash==foreign


def test_password_verification_reloads_stale_session_identity(crm):
    _, factory, _ = crm
    with factory() as db:
        db.get(User,3).password_hash=auth_utils.hash_password('old')
        db.commit()
    with auth_utils.SessionLocal() as db:
        service=IdentityService(db,ORDINARY)
        stale=service._user()
        with factory() as other:
            other.get(User,3).password_hash=auth_utils.hash_password('replaced')
            other.commit()
        with pytest.raises(PermissionError,match='Incorrect current password'):
            service.change_password({'current_password':'old','new_password':'new'})
        assert auth_utils.verify_password('replaced',stale.password_hash)


@pytest.mark.parametrize('body', [[], 'bad', {}, {'current_password':[],'new_password':'new'},
    {'current_password':'old','new_password':True}, {'current_password':'old','new_password':'é'*37}])
def test_malformed_password_changes_do_not_mutate(crm, body):
    call, factory, _ = crm
    with factory() as db:
        before=db.get(User,3).password_hash
    assert call('POST','/api/change-password',body,user=3)[0]==400
    with factory() as db:
        assert db.get(User,3).password_hash==before


def test_http_identity_uses_current_roles_and_password_boundary(crm):
    call, factory, app = crm
    with factory() as db:
        db.get(User,3).password_hash=auth_utils.hash_password('old')
        db.commit()
    me=json.loads(call('GET','/api/me',user=3)[1])
    assert me['id']==3 and me['roles']==[]
    assert json.loads(call('GET','/api/tenant/config',user=3)[1])==me['tenant']
    assert call('POST','/api/change-password',{'current_password':'wrong','new_password':'new'},user=3)[0]==403
    assert call('POST','/api/change-password',{'current_password':'old','new_password':'new','user_id':2},user=3)[0]==200
    with factory() as db:
        assert auth_utils.verify_password('new',db.get(User,3).password_hash)
        assert db.get(User,2).password_hash=='unused'
    token=jwt.encode({'alg':'HS256'},{'sub':3,'exp':int(time.time())+300},app.config['SECRET_KEY']).decode()
    async def check_cache():
        for path in ('/api/me','/api/tenant/config'):
            response=await app.test_client().get(path,headers={'Authorization':'Bearer '+token})
            assert response.headers['Cache-Control']=='no-store'
    asyncio.run(check_cache())


def test_failed_password_commit_preserves_hash_and_disables_retry(crm, monkeypatch):
    call, factory, _ = crm
    with factory() as db:
        original=auth_utils.hash_password('old')
        db.get(User,3).password_hash=original
        db.commit()
    original_factory=auth.SessionLocal
    def session():
        db=original_factory()
        def fail():
            raise OperationalError('private sql',{'password':'private'},Exception('private'),connection_invalidated=True)
        db.commit=fail
        return db
    monkeypatch.setattr(auth,'SessionLocal',session)
    status,body=call('POST','/api/change-password',{'current_password':'old','new_password':'new'},user=3)
    assert status==503 and not json.loads(body)['retryable'] and 'private' not in body
    with factory() as db:
        assert db.get(User,3).password_hash==original


def test_concurrent_password_changes_cannot_both_use_the_old_password(crm):
    _, factory, _ = crm
    with factory() as db:
        if db.bind.dialect.name != 'postgresql':
            pytest.skip('PostgreSQL row locking required')
        db.get(User,3).password_hash=auth_utils.hash_password('old')
        db.commit()
    started=Event()
    def second_change():
        with auth_utils.SessionLocal() as db:
            started.set()
            try:
                IdentityService(db,ORDINARY).change_password({'current_password':'old','new_password':'second'})
            except PermissionError:
                return 'denied'
            db.commit()
            return 'changed'
    with ThreadPoolExecutor(max_workers=1) as pool:
        with auth_utils.SessionLocal() as first:
            IdentityService(first,ORDINARY).change_password({'current_password':'old','new_password':'first'})
            result=pool.submit(second_change)
            try:
                assert started.wait(5)
                with pytest.raises(TimeoutError): result.result(timeout=0.2)
                first.commit()
            finally:
                first.rollback()
        assert result.result(timeout=10)=='denied'
    with factory() as db:
        assert auth_utils.verify_password('first',db.get(User,3).password_hash)
