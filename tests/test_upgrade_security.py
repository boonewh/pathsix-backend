import asyncio
import base64
import hashlib
import hmac
import io
import json
import logging
import ssl
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from authlib.jose import JoseError
from quart import Quart
from app.utils.auth_utils import decode_token, create_token, generate_reset_token, verify_reset_token, hash_password, verify_password
from app.utils import email_utils
from test_security_boundaries import crm


def signed_legacy_token(subject, key, expiry=None):
    def encoded(data):
        return base64.urlsafe_b64encode(json.dumps(data, separators=(',', ':')).encode()).rstrip(b'=')
    payload = {'sub':subject, 'email':'old@example.test', 'roles':['admin'], 'exp':expiry or int(time.time())+300}
    message=encoded({'alg':'HS256'})+b'.'+encoded(payload)
    signature=base64.urlsafe_b64encode(hmac.new(key.encode(), message, hashlib.sha256).digest()).rstrip(b'=')
    return (message+b'.'+signature).decode()


def test_legacy_tokens_current_tokens_passwords_and_reset_roundtrip():
    app=Quart(__name__)
    app.config['SECRET_KEY']='upgrade-test-signing-key'
    async def run():
        async with app.app_context():
            for subject in (7, '7'):
                assert decode_token(signed_legacy_token(subject, app.secret_key))['sub'] == subject
            current=create_token(SimpleNamespace(id=7,email='user@example.test',roles=[]))
            assert decode_token(current)['sub'] == 7
            with pytest.raises(JoseError):
                decode_token(signed_legacy_token(7,'wrong-key'))
            with pytest.raises(JoseError):
                decode_token(signed_legacy_token(7,app.secret_key,1))
            with pytest.raises(JoseError):
                decode_token(signed_legacy_token('not-a-user',app.secret_key))
            token=generate_reset_token('user@example.test')
            assert verify_reset_token(token)=='user@example.test'
            assert verify_reset_token(token+'tamper') is None
            hashed=hash_password('correct-test-password')
            assert verify_password('correct-test-password',hashed)
            assert not verify_password('wrong-password',hashed)
    asyncio.run(run())


def test_mail_goes_to_resend_over_verified_tls(monkeypatch):
    seen={}
    class Reply:
        def __enter__(self): return self
        def __exit__(self,*a): return False
        def read(self): return b'{"id":"email-1"}'
    def urlopen(request,timeout,context):
        seen.update(request=request,timeout=timeout,context=context)
        return Reply()
    monkeypatch.setattr(email_utils,'RESEND_API_KEY','re_test_key')
    monkeypatch.setattr(email_utils.urllib.request,'urlopen',urlopen)
    assert asyncio.run(email_utils.send_email('Test subject','visitor@example.test','Test body'))=='email-1'
    request=seen['request']
    assert request.full_url=='https://api.resend.com/emails' and request.get_method()=='POST'
    assert request.get_header('Authorization')=='Bearer re_test_key'
    payload=json.loads(request.data)
    assert payload=={'from':f'{email_utils.MAIL_FROM_NAME} <{email_utils.MAIL_FROM_EMAIL}>',
                     'to':['visitor@example.test'],'subject':'Test subject','text':'Test body'}
    assert seen['context'].check_hostname and seen['context'].verify_mode==ssl.CERT_REQUIRED
    assert seen['timeout']==15
    monkeypatch.setattr(email_utils,'MAIL_REPLY_TO','owner@example.test')
    asyncio.run(email_utils.send_email('Test subject','visitor@example.test','Test body'))
    assert json.loads(seen['request'].data)['reply_to']==['owner@example.test']


def test_mail_without_a_resend_key_fails_clearly(monkeypatch):
    monkeypatch.setattr(email_utils,'RESEND_API_KEY','')
    with pytest.raises(email_utils.EmailDeliveryError,match='RESEND_API_KEY'):
        asyncio.run(email_utils.send_email('s','visitor@example.test','b'))


def test_resend_refusal_keeps_only_the_error_code(monkeypatch):
    body=b'{"statusCode":422,"name":"validation_error","message":"Invalid `to` field: private@example.test"}'
    def urlopen(request,timeout,context):
        raise email_utils.urllib.error.HTTPError(request.full_url,422,'Unprocessable',{},io.BytesIO(body))
    monkeypatch.setattr(email_utils,'RESEND_API_KEY','re_secret_key')
    monkeypatch.setattr(email_utils.urllib.request,'urlopen',urlopen)
    with pytest.raises(email_utils.EmailDeliveryError) as refused:
        asyncio.run(email_utils.send_email('s','private@example.test','b'))
    assert str(refused.value)=='Resend refused the email (422 validation_error)'
    assert 're_secret_key' not in str(refused.value) and 'private@' not in str(refused.value)


def test_password_reset_failure_logs_no_link_recipient_or_key(crm,monkeypatch,caplog,capsys):
    # Ported from production's test for the Resend path: a refused reset email
    # must not put the reset link, the recipient or the key into logs.
    call,_,app=crm
    app.config['FRONTEND_URL']='https://crm.example.test'
    monkeypatch.setattr(email_utils,'generate_reset_token',lambda email:'private-reset-token-sentinel')
    monkeypatch.setattr(email_utils,'RESEND_API_KEY','re_private_key_sentinel')
    body=b'{"name":"validation_error","message":"Invalid `to` field: ordinary@example.test"}'
    def urlopen(request,timeout,context):
        raise email_utils.urllib.error.HTTPError(request.full_url,422,'Unprocessable',{},io.BytesIO(body))
    monkeypatch.setattr(email_utils.urllib.request,'urlopen',urlopen)
    with caplog.at_level(logging.DEBUG):
        status,_=call('POST','/api/forgot-password',{'email':'ordinary@example.test'},user=0)
    assert status==503
    logs=caplog.text+''.join(capsys.readouterr())
    assert 'validation_error' in logs
    for private in ('private-reset-token-sentinel','ordinary@example.test','re_private_key_sentinel','reset-password/'):
        assert private not in logs