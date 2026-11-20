"""Tests for the mobile-only password bootstrap script (Phase 1 test sign-in).

The script is an operator tool: it must only run on the mobile Fly app against
the mobile database as its restricted runtime login, reject weak passwords
before touching a database, update one active user under that user's tenant RLS
context, and never expose the password in its output.
"""
import io
import sys
from contextlib import nullcontext
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import set_mobile_test_password as bootstrap  # noqa: E402

from app.models import Tenant, User  # noqa: E402
from app.utils.auth_utils import hash_password, verify_password  # noqa: E402
from test_security_boundaries import crm as crm  # noqa: F401,E402
from test_security_boundaries import rls_runtime as rls_runtime  # noqa: F401,E402

PASSWORD = 'Phase1TestPass!123'
MOBILE_SPEC = bootstrap.mobile_target()
MOBILE_URL = (f"postgresql://{MOBILE_SPEC['runtime_role']}:pw@"
              f"pathsixsolutions-db-mobile.flycast:5432/{MOBILE_SPEC['database']}")
WRONG_DATABASE_MESSAGE = 'Refusing: DATABASE_URL is not the mobile database as its restricted runtime login.'
OVERRIDE_MESSAGE = 'Refusing a database URL with connection overrides.'
UNSAFE_CONNECTION_MESSAGE = 'Refusing: the connection is not the restricted mobile runtime login with row security.'
FIXED_ERROR_MESSAGE = 'Database error; nothing was changed.'


def _mobile_url(*, host=None, database=None, user=None, query=''):
    return (f"postgresql://{user or MOBILE_SPEC['runtime_role']}:pw@"
            f"{host or 'pathsixsolutions-db-mobile.flycast'}:5432/"
            f"{database or MOBILE_SPEC['database']}{query}")


@pytest.mark.parametrize('app', [
    None,
    '',
    'pathsixsolutions-backend',
    'pathsixsolutions-backend-staging',
    'pathsixsolutions-backend-other',
])
def test_refuses_outside_the_mobile_app(monkeypatch, capsys, app):
    monkeypatch.setenv('DATABASE_URL', MOBILE_URL)
    if app is None:
        monkeypatch.delenv('FLY_APP_NAME', raising=False)
    else:
        monkeypatch.setenv('FLY_APP_NAME', app)

    with pytest.raises(SystemExit, match='Refusing to run outside pathsixsolutions-backend-mobile'):
        bootstrap.main(['--email', 'a@example.test'], stdin=io.StringIO(PASSWORD + '\n'))

    printed = capsys.readouterr()
    assert PASSWORD not in printed.out + printed.err


@pytest.mark.parametrize('url,message', [
    (_mobile_url(host='pathsixsolutions-db.flycast'), WRONG_DATABASE_MESSAGE),
    (_mobile_url(database='pathsixsolutions_backend'), WRONG_DATABASE_MESSAGE),
    (_mobile_url(user='pathsix_crm_runtime'), WRONG_DATABASE_MESSAGE),
    (_mobile_url(user=MOBILE_SPEC['admin_user']), WRONG_DATABASE_MESSAGE),
    (_mobile_url(query='?options=-csearch_path%3Dpublic'), OVERRIDE_MESSAGE),
    (_mobile_url(query='?host=/tmp'), OVERRIDE_MESSAGE),
])
def test_main_refuses_wrong_mobile_database_target(monkeypatch, capsys, url, message):
    monkeypatch.setenv('FLY_APP_NAME', bootstrap.APP)
    monkeypatch.setenv('DATABASE_URL', url)

    def unexpected_engine(*args, **kwargs):
        raise AssertionError('An invalid database target must be refused before connecting')

    monkeypatch.setattr('sqlalchemy.create_engine', unexpected_engine)
    with pytest.raises(SystemExit, match=message):
        bootstrap.main(['--email', 'a@example.test'], stdin=io.StringIO(PASSWORD + '\n'))

    printed = capsys.readouterr()
    assert PASSWORD not in printed.out + printed.err


def test_checked_runtime_url_allows_only_sslmode():
    assert bootstrap.checked_runtime_url(_mobile_url(query='?sslmode=require')).query == {'sslmode': 'require'}


class _StubResult:
    def __init__(self, row):
        self._row = row

    def one(self):
        return self._row


class _StubConnection:
    def __init__(self, row=None, error=None):
        self.row = row or (MOBILE_SPEC['database'], MOBILE_SPEC['runtime_role'], False, True)
        self.error = error
        self.executed = []

    def execute(self, statement, params=None):
        self.executed.append((str(statement), params))
        if self.error is not None:
            raise self.error
        return _StubResult(self.row)


def _verification_row(**overrides):
    row = {
        'database': MOBILE_SPEC['database'],
        'user': MOBILE_SPEC['runtime_role'],
        'bypass': False,
        'rls': True,
    }
    row.update(overrides)
    return tuple(row.values())


@pytest.mark.parametrize('overrides', [
    {'database': 'pathsixsolutions_backend_mobile_copy'},
    {'user': MOBILE_SPEC['admin_user']},
    {'bypass': True},
    {'rls': False},
])
def test_verify_connection_refuses_unsafe_connections(overrides):
    connection = _StubConnection(_verification_row(**overrides))
    with pytest.raises(bootstrap.Refused, match='Refusing: the connection is not the restricted mobile runtime login with row security.'):
        bootstrap.verify_connection(connection)


def test_verify_connection_accepts_the_restricted_runtime_login():
    assert bootstrap.verify_connection(_StubConnection()) is None


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
    def __init__(self, connection):
        self.connection = connection
        self.disposed = False

    def begin(self):
        return nullcontext(self.connection)

    def dispose(self):
        self.disposed = True


def test_main_never_prints_the_password(monkeypatch, capsys):
    monkeypatch.setenv('FLY_APP_NAME', bootstrap.APP)
    monkeypatch.setenv('DATABASE_URL', MOBILE_URL)
    engine = _FakeEngine(_StubConnection())
    monkeypatch.setattr('sqlalchemy.create_engine', lambda *args, **kwargs: engine)
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
    assert printed.out == 'Password set for user 7. Their existing phones are signed out.\n'
    assert seen['email'] == 'a@example.test'
    assert verify_password(PASSWORD, seen['hash'])
    assert engine.disposed


def test_main_reports_only_the_fixed_database_error(monkeypatch, capsys):
    monkeypatch.setenv('FLY_APP_NAME', bootstrap.APP)
    monkeypatch.setenv('DATABASE_URL', MOBILE_URL)
    secret = f'SQL text with {PASSWORD} and a bcrypt hash'
    engine = _FakeEngine(_StubConnection(error=SQLAlchemyError(secret)))
    monkeypatch.setattr('sqlalchemy.create_engine', lambda *args, **kwargs: engine)

    with pytest.raises(SystemExit) as exc:
        bootstrap.main(['--email', 'a@example.test'], stdin=io.StringIO(PASSWORD + '\n'))

    printed = capsys.readouterr()
    assert exc.value.code == FIXED_ERROR_MESSAGE
    assert secret not in str(exc.value)
    assert PASSWORD not in str(exc.value)
    assert printed.out == ''
    assert PASSWORD not in printed.err
    assert engine.disposed


def test_set_password_updates_only_that_active_user_under_rls(rls_runtime):
    """The script scopes its own update, even from an unbound restricted session."""
    runtime, admin, _ = rls_runtime
    before_password = 'BeforePass!123'
    with admin() as db:
        db.add_all([
            Tenant(id=3, name='C', slug='c', is_active=False),
            User(id=4, tenant_id=2, email='inactive2@example.test',
                 password_hash=hash_password(before_password), is_active=False),
            User(id=5, tenant_id=3, email='inactive-tenant@example.test',
                 password_hash=hash_password(before_password), is_active=True),
        ])
        db.flush()
        for user in db.query(User).all():
            user.password_hash = hash_password(before_password)
        db.commit()

    def user_snapshot():
        with admin() as db:
            return {user.id: (user.email, user.tenant_id, user.is_active, user.password_hash)
                    for user in db.query(User).order_by(User.id).all()}

    def tenant_snapshot():
        with admin() as db:
            return {tenant.id: (tenant.name, tenant.slug, tenant.is_active)
                    for tenant in db.query(Tenant).order_by(Tenant.id).all()}

    before_users = user_snapshot()
    before_tenants = tenant_snapshot()
    new_hash = hash_password(PASSWORD)
    assert before_users[2][2] is True and before_users[2][1] == 2
    assert before_users[4][2] is False and before_users[4][1] == 2
    assert before_users[5][2] is True and before_users[5][1] == 3
    assert before_tenants[2][2] is True and before_tenants[3][2] is False

    # No Principal is bound: apply_context sets an empty tenant, so a successful
    # tenant-2 write can only come from set_password's identity lookup and
    # set_config. The target user is active in an active tenant.
    with runtime() as db:
        assert db.info.get('principal') is None
        assert db.execute(text("SELECT current_setting('crm.tenant_id', true)")).scalar_one() == ''
        connection = db.connection()
        assert bootstrap.set_password(connection, 'b@example.test', new_hash) == 2
        assert db.execute(text("SELECT current_setting('crm.tenant_id', true)")).scalar_one() == '2'
        db.commit()

    # Unknown addresses, inactive users and users in inactive tenants are all
    # refused, with no write. The last one needs the UPDATE's active-tenant
    # recheck: the identity function still finds that active user.
    with runtime() as db:
        assert db.info.get('principal') is None
        connection = db.connection()
        assert bootstrap.set_password(connection, 'missing@example.test', new_hash) is None
        assert bootstrap.set_password(connection, 'inactive2@example.test', new_hash) is None
        assert bootstrap.set_password(connection, 'inactive-tenant@example.test', new_hash) is None
        db.commit()

    after_users = user_snapshot()
    after_tenants = tenant_snapshot()
    assert set(after_users) == set(before_users)
    for user_id, previous in before_users.items():
        if user_id == 2:
            assert after_users[user_id][:3] == previous[:3]
            assert after_users[user_id][3] != previous[3]
            assert verify_password(PASSWORD, after_users[user_id][3])
        else:
            assert after_users[user_id] == previous, user_id
    assert after_tenants == before_tenants
