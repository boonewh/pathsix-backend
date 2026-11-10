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


def test_resend_refusal_is_reported_without_the_key(monkeypatch):
    def urlopen(request,timeout,context):
        raise email_utils.urllib.error.HTTPError(request.full_url,403,'Forbidden',{},
            io.BytesIO(b'{"message":"The pathsixdesigns.com domain is not verified"}'))
    monkeypatch.setattr(email_utils,'RESEND_API_KEY','re_secret_key')
    monkeypatch.setattr(email_utils.urllib.request,'urlopen',urlopen)
    with pytest.raises(email_utils.EmailDeliveryError) as refused:
        asyncio.run(email_utils.send_email('s','visitor@example.test','b'))
    assert '403' in str(refused.value) and 'not verified' in str(refused.value)
    assert 're_secret_key' not in str(refused.value)


