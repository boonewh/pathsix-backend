import asyncio
import base64
import hashlib
import hmac
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


def test_mail_keeps_verified_tls_and_existing_envelope(monkeypatch):
    send=AsyncMock()
    monkeypatch.setattr(email_utils.aiosmtplib,'send',send)
    asyncio.run(email_utils.send_email('Test subject','visitor@example.test','Test body'))
    args=send.call_args
    assert args.args[0]['To']=='visitor@example.test'
    assert args.kwargs['hostname']==email_utils.MAIL_SERVER
    assert args.kwargs['start_tls']==email_utils.MAIL_USE_TLS
    assert args.kwargs['validate_certs'] is True
    assert args.kwargs['tls_context'].check_hostname
    assert args.kwargs['tls_context'].verify_mode==ssl.CERT_REQUIRED
    assert args.kwargs['timeout']==15


