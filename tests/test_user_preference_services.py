"""Direct and HTTP user/preference authorization and transaction boundaries."""
import asyncio
import json
import time
from unittest.mock import AsyncMock

import pytest
from authlib.jose import jwt
from sqlalchemy.exc import IntegrityError, OperationalError

from test_security_boundaries import crm
from app.models import User, UserPreference
from app.routes import users, user_preferences
from app.services.errors import RecordNotFound
from app.services.preferences import PreferenceService
from app.services.principal import Principal
from app.services.users import UserService
from app.utils import auth_utils

ADMIN = Principal(1, 1, frozenset({'admin'}))
ORDINARY = Principal(3, 1, frozenset())


@pytest.mark.parametrize('restricted', [False, True])
def test_user_service_scopes_all_operations_and_requires_admin(crm, restricted):
    _, factory, _ = crm
    runtime = auth_utils.SessionLocal if restricted else factory
    with runtime() as db:
        with pytest.raises(TypeError):
            UserService(db, None)
    with runtime() as db:
        with pytest.raises(PermissionError):
            UserService(db, ORDINARY)
    with runtime() as db:
        service = UserService(db, ADMIN)
        assert {u['id'] for u in service.list()} == {1, 3}
        assert all('password_hash' not in u and 'tenant_id' not in u for u in service.list())
        for ident in (2, 99999):
            for operation in (lambda: service.toggle_active(ident),
                              lambda: service.update_roles(ident, {'roles':['admin']}),
                              lambda: service.update_email(ident, {'email':'changed@example.test'}),
                              lambda: service.password_reset_recipient(ident)):
                with pytest.raises(RecordNotFound, match='User not found'):
                    operation()
        with pytest.raises(PermissionError, match='cannot deactivate yourself'):
            service.toggle_active(1)
    with factory() as db:
        assert db.get(User, 2).email == 'b@example.test' and db.get(User, 2).is_active


@pytest.mark.parametrize('restricted', [False, True])
def test_user_mutations_are_caller_owned_and_ignore_forged_identity(crm, restricted):
    _, factory, _ = crm
    runtime = auth_utils.SessionLocal if restricted else factory
    with runtime() as db:
        service = UserService(db, ADMIN)
        created = service.create({'email':'new@example.test', 'password':'test-password',
                                  'roles':['admin'], 'tenant_id':2, 'is_active':False})
        assert created['is_active'] and created['roles'] == ['admin']
        assert auth_utils.verify_password('test-password', db.get(User, created['id']).password_hash)
        assert db.get(User, created['id']).tenant_id == 1
        service.update_email(3, {'email':'changed@example.test', 'tenant_id':2})
        service.update_roles(3, {'roles':['admin']})
        service.toggle_active(3)
        db.rollback()
    with factory() as db:
        assert db.query(User).count() == 3
        user = db.get(User, 3)
        assert user.email == 'ordinary@example.test' and user.is_active and not user.roles


@pytest.mark.parametrize('roles', [['admin','missing'], 'admin', [None], [1], [{}], None])
def test_invalid_roles_reject_complete_mutation(crm, roles):
    call, factory, _ = crm
    assert call('POST','/api/users', {'email':'new@example.test','password':'valid','roles':roles})[0] == 400
    assert call('PUT','/api/users/3/roles', {'roles':roles})[0] == 400
    with factory() as db:
        assert db.query(User).count() == 3 and not db.get(User, 3).roles


def test_email_collisions_are_safe_for_visible_and_hidden_accounts(crm):
    call, factory, _ = crm
    errors = []
    for email in ('a@example.test', 'b@example.test'):
        status, body = call('POST','/api/users', {'email':email,'password':'test-password','roles':['admin']})
        assert status == 400
        errors.append(json.loads(body))
        status, body = call('PUT','/api/users/3', {'email':email})
        assert status == 400
        errors.append(json.loads(body))
    assert all(e == {'error':'Email is unavailable'} for e in errors)
    with factory() as db:
        assert db.query(User).count() == 3 and db.get(User, 3).email == 'ordinary@example.test'


def test_user_http_lifecycle_and_current_role_enforcement(crm, monkeypatch):
    call, factory, _ = crm
    delivery = AsyncMock()
    monkeypatch.setattr(users, 'send_password_reset_email', delivery)
    for ident in (2, 99999):
        for method, suffix, body in [('PUT','',{'email':'x@example.test'}),
                                    ('PUT','/roles',{'roles':[]}),
                                    ('PUT','/toggle-active',{}),
                                    ('POST','/send-password-reset',{})]:
            assert call(method,f'/api/users/{ident}'+suffix,body)[0] == 404
    delivery.assert_not_awaited()
    assert call('PUT','/api/users/1/toggle-active')[0] == 403
    assert call('GET','/api/users',user=3)[0] == 403
    status, body = call('POST','/api/users/',{'email':'new@example.test','password':'safe','roles':[]})
    assert status == 201
    ident = json.loads(body)['id']
    assert call('PUT',f'/api/users/{ident}',{'email':'renamed@example.test'})[0] == 200
    assert call('PUT',f'/api/users/{ident}/roles',{'roles':['admin']})[0] == 200
    assert call('GET','/api/users',user=ident)[0] == 200
    assert call('PUT',f'/api/users/{ident}/roles',{'roles':[]})[0] == 200
    assert call('GET','/api/users',user=ident)[0] == 403
    assert call('PUT',f'/api/users/{ident}/toggle-active')[0] == 200
    assert call('GET','/api/preferences',user=ident)[0] == 401
    with factory() as db:
        assert not db.get(User,ident).is_active


@pytest.mark.parametrize('body', [[], 'invalid', {'email':[]}, {'email':'bad', 'password':'x'},
    {'email':'new@example.test','password':123}, {'email':'new@example.test','password':'é'*37}])
def test_malformed_user_requests_have_safe_validation_errors(crm, body):
    call, factory, _ = crm
    assert call('POST','/api/users',body)[0] == 400
    if not isinstance(body, dict) or body.get('email') != 'new@example.test':
        assert call('PUT','/api/users/3',body)[0] == 400
    with factory() as db:
        assert db.query(User).count() == 3


@pytest.mark.parametrize('restricted', [False, True])
def test_preferences_are_personal_in_same_and_foreign_tenants(crm, restricted):
    _, factory, _ = crm
    with factory() as db:
        for user, size in ((1,21),(2,22),(3,23)):
            db.add(UserPreference(user_id=user,category='pagination',preference_key='leads',
                                  preference_value={'perPage':size}))
        db.commit()
    runtime = auth_utils.SessionLocal if restricted else factory
    for principal, size in ((ADMIN,21), (ORDINARY,23), (Principal(2,2,frozenset()),22)):
        with runtime() as db:
            service = PreferenceService(db, principal)
            result = service.get()
            assert result['pagination']['leads'] == {'perPage':size,'sort':'newest','viewMode':'cards'}
            result['pagination']['clients']['perPage'] = 99
            assert service.get()['pagination']['clients']['perPage'] == 10
            service.update_pagination('leads', {'perPage':30,'user_id':2,'tenant_id':2})
            assert service.get()['pagination']['leads']['perPage'] == 30
            db.rollback()
    with factory() as db:
        assert sorted(p.preference_value['perPage'] for p in db.query(UserPreference)) == [21,22,23]
    with runtime() as db:
        with pytest.raises(PermissionError):
            PreferenceService(db, Principal(2,1,frozenset({'admin'})))


def test_preferences_http_upsert_and_identity_are_preserved(crm):
    call, factory, _ = crm
    for size in (17,31):
        status, body = call('PUT','/api/preferences/pagination/leads',
                           {'perPage':size,'sort':'oldest','viewMode':'table','user_id':2},user=3)
        assert status == 200 and json.loads(body)['preference']['perPage'] == size
    assert json.loads(call('GET','/api/preferences/',user=3)[1])['pagination']['leads']['perPage'] == 31
    assert json.loads(call('GET','/api/preferences')[1])['pagination']['leads']['perPage'] == 10
    with factory() as db:
        prefs = db.query(UserPreference).all()
        assert len(prefs) == 1 and prefs[0].user_id == 3


@pytest.mark.parametrize('body', [[], 'invalid', {'perPage':True}, {'perPage':0},
                                 {'perPage':101}, {'perPage':'10'}, {'sort':[]}])
def test_invalid_preferences_leave_no_rows(crm, body):
    assert crm[0]('PUT','/api/preferences/pagination/leads',body,user=3)[0] == 400
    with crm[1]() as db:
        assert db.query(UserPreference).count() == 0


def test_preferences_defaults_fallback_and_table_validation(crm):
    call, _, _ = crm
    assert call('PUT','/api/preferences/pagination/unknown',{},user=3)[0] == 400
    status, body = call('PUT','/api/preferences/pagination/interactions',{'viewMode':'unknown'},user=3)
    assert status == 200 and json.loads(body)['preference'] == {'perPage':10,'sort':'newest','viewMode':'cards'}


@pytest.mark.parametrize('resource', ['users', 'preferences'])
def test_commit_failure_rolls_back_and_hides_database_details(crm, monkeypatch, resource):
    call, factory, _ = crm
    module = users if resource == 'users' else user_preferences
    original = module.SessionLocal
    def failing_session():
        db = original()
        def fail():
            raise OperationalError('private sql', {'password':'private'}, Exception('private provider error'))
        db.commit = fail
        return db
    monkeypatch.setattr(module,'SessionLocal',failing_session)
    if resource == 'users':
        status, body = call('POST','/api/users',{'email':'new@example.test','password':'safe'})
    else:
        status, body = call('PUT','/api/preferences/pagination/leads',{'perPage':20},user=3)
    assert status == 500 and 'private' not in body
    with factory() as db:
        assert db.query(User).count() == 3 and db.query(UserPreference).count() == 0


def test_preference_insert_rolls_back_and_inactive_owner_is_denied(crm):
    _, factory, _ = crm
    with auth_utils.SessionLocal() as db:
        PreferenceService(db, ORDINARY).update_pagination('leads', {'perPage':24})
        db.rollback()
    with factory() as db:
        assert db.query(UserPreference).count() == 0
        db.get(User,3).is_active = False
        db.commit()
    with auth_utils.SessionLocal() as db:
        with pytest.raises(PermissionError):
            PreferenceService(db, ORDINARY)


def test_preference_insert_race_returns_conflict_without_private_details(crm, monkeypatch):
    original = PreferenceService.update_pagination
    def collision(service, table, data):
        original(service, table, data)
        raise IntegrityError('private sql', {'private':'value'}, Exception('private constraint'))
    monkeypatch.setattr(PreferenceService,'update_pagination',collision)
    status, body = crm[0]('PUT','/api/preferences/pagination/leads',{'perPage':20},user=3)
    assert status == 409 and 'private' not in body
    with crm[1]() as db:
        assert db.query(UserPreference).count() == 0


def test_disconnected_user_write_is_not_advertised_as_safe_to_retry(crm, monkeypatch):
    def disconnect(service, data):
        raise OperationalError('private sql', {}, Exception('private'), connection_invalidated=True)
    monkeypatch.setattr(UserService,'create',disconnect)
    status, body = crm[0]('POST','/api/users',{'email':'new@example.test','password':'safe'})
    assert status == 503 and not json.loads(body)['retryable'] and 'private' not in body


def test_user_and_preference_reads_are_not_cached(crm):
    app = crm[2]
    token = jwt.encode({'alg':'HS256'}, {'sub':1,'exp':int(time.time())+300},app.config['SECRET_KEY']).decode()
    async def exercise():
        for path in ('/api/users','/api/preferences'):
            response = await app.test_client().get(path,headers={'Authorization':'Bearer '+token})
            assert response.status_code == 200 and response.headers['Cache-Control'] == 'no-store'
    asyncio.run(exercise())
