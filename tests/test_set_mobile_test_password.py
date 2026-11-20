"""Tests for the mobile-only password bootstrap script (Phase 1 test sign-in).

The script is an operator tool: it must only run on the mobile Fly app, reject
weak passwords before touching a database, update one active user under that
user's tenant RLS context, and never expose the password in its output.
"""
import io
import sys
from contextlib import nullcontext
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import set_mobile_test_password as bootstrap  # noqa: E402

from app.models import User  # noqa: E402
from app.services.database_context import bind_principal  # noqa: E402
from app.services.principal import Principal  # noqa: E402
from app.utils.auth_utils import hash_password, verify_password  # noqa: E402
from test_security_boundaries import crm as crm  # noqa: F401,E402
from test_security_boundaries import rls_runtime as rls_runtime  # noqa: F401,E402

PASSWORD = 'Phase1TestPass!123'


@pytest.mark.parametrize('app', [
    None,
    '',
    'pathsixsolutions-backend',
    'pathsixsolutions-backend-staging',
    'pathsixsolutions-backend-other',
])
def test_refuses_outside_the_mobile_app(monkeypatch, capsys, app):
    monkeypatch.setenv('DATABASE_URL', 'postgresql://runtime:x@db.internal:5432/app')
    if app is None:
        monkeypatch.delenv('FLY_APP_NAME', raising=False)
    else:
        monkeypatch.setenv('FLY_APP_NAME', app)

    with pytest.raises(SystemExit, match='Refusing to run outside pathsixsolutions-backend-mobile'):
        bootstrap.main(['--email', 'a@example.test'], stdin=io.StringIO(PASSWORD + '\n'))

    printed = capsys.readouterr()
    assert PASSWORD not in printed.out + printed.err


@pytest.mark.parametrize('password,valid', [
    ('short', False),          # below 12 characters
    ('a' * 11, False),
    ('a' * 12, True),          # the minimum
    ('a' * 72, True),          # bcrypt's byte maximum
    ('a' * 73, False),         # over the byte maximum
    ('é' * 36, True),          # 36 characters but exactly 72 UTF-8 bytes
    ('é' * 37, False),         # 37 characters but 74 UTF-8 bytes
])
def test_read_password_enforces_length_and_utf8_byte_limit(password, valid):
    stream = io.StringIO(password + '\n')
    if valid:
        assert bootstrap.read_password(stream) == password
    else:
        with pytest.raises(SystemExit, match='at least 12 characters and at most 72 bytes'):
            bootstrap.read_password(stream)


class _FakeEngine:
    def begin(self):
        return nullcontext(object())

    def dispose(self):
        pass


def test_main_never_prints_the_password(monkeypatch, capsys):
    monkeypatch.setenv('FLY_APP_NAME', bootstrap.APP)
    monkeypatch.setenv('DATABASE_URL', 'postgresql://runtime:x@db.internal:5432/app')
    monkeypatch.setattr('sqlalchemy.create_engine', lambda *args, **kwargs: _FakeEngine())
    seen = {}

    def fake_set_password(connection, email, password_hash):
        seen['email'] = email
        seen['hash'] = password_hash
        return 7

    monkeypatch.setattr(bootstrap, 'set_password', fake_set_password)
    bootstrap.main(['--email', ' A@Example.test '], stdin=io.StringIO(PASSWORD + '\n'))

    printed = capsys.readouterr()
    assert PASSWORD not in printed.out
    assert PASSWORD not in printed.err
    assert 'Password set for user 7.' in printed.out
    assert seen['email'] == 'a@example.test'
    assert verify_password(PASSWORD, seen['hash'])


def test_set_password_updates_only_that_active_user_under_rls(rls_runtime):
    """A proved operator update stays inside the target user's tenant and row."""
    runtime, admin, _ = rls_runtime
    before_password = 'BeforePass!123'
    with admin() as db:
        for user_id in (1, 2, 3):
            db.get(User, user_id).password_hash = hash_password(before_password)
        db.add(User(tenant_id=2, email='inactive2@example.test',
                    password_hash=hash_password(before_password), is_active=False))
        db.commit()

    def snapshot():
        with admin() as db:
            return {user.email: (user.tenant_id, user.is_active, user.password_hash)
                    for user in db.query(User).all()}

    before = snapshot()
    new_hash = hash_password(PASSWORD)

    # A tenant-1 identity can bootstrap another active tenant-1 user; the
    # identity function and the script's own tenant context do the narrow write.
    with runtime() as db:
        bind_principal(db, Principal(3, 1, frozenset({'admin'})))
        assert bootstrap.set_password(db.connection(), 'a@example.test', new_hash) == 1
        db.commit()

    # Unknown and inactive addresses are refusals, not updates. The inactive row
    # lives in tenant 2, so an update there would also be a cross-tenant write.
    with runtime() as db:
        bind_principal(db, Principal(3, 1, frozenset({'admin'})))
        connection = db.connection()
        assert bootstrap.set_password(connection, 'missing@example.test', new_hash) is None
        assert bootstrap.set_password(connection, 'inactive2@example.test', new_hash) is None
        db.commit()

    after = snapshot()
    assert set(after) == set(before)
    for email, previous in before.items():
        if email == 'a@example.test':
            assert after[email][:2] == previous[:2]
            assert after[email][2] != previous[2]
            assert verify_password(PASSWORD, after[email][2])
        else:
            assert after[email] == previous, email
