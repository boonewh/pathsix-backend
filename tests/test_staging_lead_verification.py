"""Staging verifier must reject unsafe targets before opening a connection."""
import io
import json
import pytest
from sqlalchemy import text
from scripts import verify_staging_lead_creation as verifier
from test_security_boundaries import crm

REVISION = 'a' * 40
OPERATOR = 'postgresql://pathsixsolutions_backend_staging:synthetic@pathsixsolutions-db-staging.internal/pathsixsolutions_backend_staging'


def environment():
    return {'APP_REVISION': REVISION, 'FLY_APP_NAME': verifier.APP, 'CRM_RLS_ENABLED': '1',
            'MCP_RESOURCE_URI': f'https://{verifier.APP}.fly.dev/mcp',
            'DATABASE_URL': OPERATOR.replace('pathsixsolutions_backend_staging:synthetic',
                                             'pathsix_crm_staging_runtime:synthetic')}


@pytest.mark.parametrize('key,value', [
    ('APP_REVISION', 'b' * 40), ('FLY_APP_NAME', 'pathsixsolutions-backend'),
    ('CRM_RLS_ENABLED', '0'), ('MCP_RESOURCE_URI', 'https://example.test/mcp'),
    ('CRM_PLATFORM_JOB_OPERATIONS', 'cleanup'), ('DATABASE_URL', OPERATOR),
    ('DATABASE_URL', OPERATOR.replace('.internal', '.invalid')),
])
def test_wrong_environment_is_rejected(key, value):
    env = environment()
    env[key] = value
    with pytest.raises(RuntimeError):
        verifier.require_environment(REVISION, OPERATOR, env)


@pytest.mark.parametrize('url', [
    OPERATOR.replace('.internal', '.invalid'),
    OPERATOR.replace('/pathsixsolutions_backend_staging', '/production'),
    OPERATOR.replace('pathsixsolutions_backend_staging:synthetic', 'postgres:synthetic'),
    OPERATOR + '?options=-csearch_path%3Dpublic',
    OPERATOR.replace('.internal/', '.internal:5433/'),
    'sqlite:///staging.db',
])
def test_wrong_operator_target_is_rejected(url):
    with pytest.raises(RuntimeError):
        verifier.require_environment(REVISION, url, environment())


def test_valid_environment_and_private_test_endpoint():
    operator, runtime = verifier.require_environment(REVISION,
        OPERATOR.replace('.internal', '.flycast'), environment())
    assert operator.host == 'pathsixsolutions-db-staging.internal'
    assert runtime.username == verifier.ROLE
    with pytest.raises(RuntimeError):
        verifier.require_environment('short', OPERATOR, environment())


def test_main_rejects_wrong_revision_before_database_access(monkeypatch, tmp_path):
    for key, value in environment().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(verifier.sys, 'argv', ['verify', '--revision', 'b' * 40,
                                             '--test-root', str(tmp_path)])
    monkeypatch.setattr(verifier.sys, 'stdin', io.StringIO(OPERATOR + '\n'))
    def forbidden(*args, **kwargs):
        pytest.fail('Database must not be opened before environment checks')
    monkeypatch.setattr(verifier, 'create_engine', forbidden)
    with pytest.raises(RuntimeError):
        verifier.main()


def test_snapshot_detects_data_changes_without_returning_record_content(crm):
    with crm[1]() as db:
        engine = db.get_bind()
        if engine.dialect.name != 'postgresql':
            pytest.skip('Requires PostgreSQL snapshot semantics')
        schema = db.execute(text('SELECT current_schema()')).scalar_one()
    before = verifier.snapshot(engine, ('leads', 'users'), schema)
    assert before == verifier.snapshot(engine, ('leads', 'users'), schema)
    assert 'Private lead' not in json.dumps(before)
    assert 'a@example.test' not in json.dumps(before)
    with engine.begin() as c:
        c.execute(text("UPDATE leads SET name='Changed synthetic lead' WHERE id=1"))
    after = verifier.snapshot(engine, ('leads', 'users'), schema)
    assert before['leads']['count'] == after['leads']['count']
    assert before['leads']['digest'] != after['leads']['digest']
    assert before['users'] == after['users']
