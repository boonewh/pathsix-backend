"""The connector release check refuses any database but its chosen target.

The live steps run against a deployed app, so these tests cover what can be
checked without one: the target table, the guards inside the remote step, and
that no secret is ever placed in an argument list.
"""
import ast
import json
import re
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'verify_production_connector.py'
sys.path.insert(0, str(SCRIPT.parent))
import verify_production_connector as check  # noqa: E402


def remote_source():
    match = re.search(r"^REMOTE = r'''(.*?)'''", SCRIPT.read_text(), re.S | re.M)
    assert match, 'the remote step source is no longer a raw string literal'
    return match.group(1)


def test_remote_step_compiles_on_its_own():
    ast.parse(remote_source())


def test_each_target_names_one_database_app_and_helper():
    assert set(check.TARGETS) == {'staging', 'production'}
    for name, spec in check.TARGETS.items():
        assert spec['database'].startswith('pathsixsolutions_backend')
        assert spec['admin_user'] == spec['database']
        assert all(host.startswith('pathsixsolutions-db') for host in spec['hosts'])
        assert name in spec['base'] or name == 'production'
    staging, production = check.TARGETS['staging'], check.TARGETS['production']
    for field in ('base', 'app', 'database', 'admin_user', 'helper_env', 'helper_default'):
        assert staging[field] != production[field], field
    assert not set(staging['hosts']) & set(production['hosts'])


@pytest.mark.parametrize('url', [
    'postgresql://pathsixsolutions_backend:x@pathsixsolutions-db-staging.flycast:5432/pathsixsolutions_backend_staging',
    'postgresql://pathsix_crm_runtime:x@pathsixsolutions-db.flycast:5432/pathsixsolutions_backend',
    'postgresql://pathsixsolutions_backend:x@pathsixsolutions-db.flycast:5432/other',
    'postgresql://pathsixsolutions_backend:x@attacker.example.com:5432/pathsixsolutions_backend',
])
def test_remote_step_refuses_a_url_outside_its_target(url, monkeypatch, capsys):
    """The production target must reject a staging, runtime-role or foreign URL."""
    source = remote_source()
    args = json.dumps({'step': 'inspect', 'target': check.TARGETS['production'],
                       'approved': 'a', 'cancelled': 'b'})

    class Stdin:
        def __init__(self): self.lines = iter([url + '\n', '{}\n'])
        def readline(self): return next(self.lines, '')

    monkeypatch.setattr(sys, 'stdin', Stdin())
    monkeypatch.setattr(sys, 'argv', ['r', args])
    with pytest.raises(SystemExit) as exit_info:
        exec(compile(source, 'remote', 'exec'), {'__name__': '__main__'})
    assert 'Refusing unexpected database target' in str(exit_info.value)
    capsys.readouterr()


def test_remote_step_accepts_its_own_target_before_connecting(monkeypatch):
    """A matching URL passes the guard and fails only at the database itself."""
    source = remote_source()
    url = ('postgresql://pathsixsolutions_backend:x@pathsixsolutions-db.flycast:5432/'
           'pathsixsolutions_backend')
    args = json.dumps({'step': 'inspect', 'target': check.TARGETS['production'],
                       'approved': 'a', 'cancelled': 'b'})

    class Stdin:
        def __init__(self): self.lines = iter([url + '\n', '{}\n'])
        def readline(self): return next(self.lines, '')

    monkeypatch.setattr(sys, 'stdin', Stdin())
    monkeypatch.setattr(sys, 'argv', ['r', args])
    with pytest.raises(Exception) as error:
        exec(compile(source, 'remote', 'exec'), {'__name__': '__main__'})
    assert 'Refusing unexpected database target' not in str(error.value)


def test_secrets_travel_on_stdin_not_in_the_command(monkeypatch):
    """The connection URL and generated password must never reach an argument list."""
    seen = {}

    class Completed:
        def __init__(self, stdout=b''): self.stdout, self.stderr, self.returncode = stdout, b'', 0

    def fake_run(command, **kwargs):
        if command[0] == 'powershell':
            return Completed(b'postgresql://user:secret-url@pathsixsolutions-db.flycast/db\n')
        seen['command'] = command
        seen['stdin'] = kwargs.get('input', b'')
        return Completed(b'{"ok": true}\n')

    monkeypatch.setattr(check.subprocess, 'run', fake_run)
    monkeypatch.setenv('PATHSIX_PRODUCTION_ADMIN_URL_HELPER', 'helper.ps1')
    result = check.remote(check.TARGETS['production'], {'step': 'inspect'}, {'password': 'top-secret'})
    assert result == {'ok': True}
    joined = ' '.join(seen['command'])
    assert 'secret-url' not in joined and 'top-secret' not in joined
    assert b'secret-url' in seen['stdin'] and b'top-secret' in seen['stdin']
    assert seen['command'][:2] == ['flyctl', 'ssh']
    assert '--app' in seen['command'] and 'pathsixsolutions-backend' in seen['command']
