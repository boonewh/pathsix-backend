"""Verify deployed lead creation using disposable test schemas, never public writes.

The staging operator URL arrives on stdin, never as an argument or saved secret.
Upload tests from the reviewed revision to a temporary directory before running.
"""
import argparse
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

APP = 'pathsixsolutions-backend-staging'
DATABASE = 'pathsixsolutions_backend_staging'
ROLE = 'pathsix_crm_staging_runtime'
HOSTS = {'pathsixsolutions-db-staging.flycast', 'pathsixsolutions-db-staging.internal'}
TESTS = ('test_mcp_lead_creation.py', 'test_mcp_lead_migration.py',
         'test_mcp_reads.py', 'test_isolation_proof.py', 'test_project_archive.py')


def require_environment(revision, operator_url, env):
    if (not re.fullmatch(r'[0-9a-f]{40}', revision)
            or env.get('APP_REVISION') != revision
            or env.get('FLY_APP_NAME') != APP
            or env.get('CRM_RLS_ENABLED') != '1'
            or env.get('MCP_RESOURCE_URI') != f'https://{APP}.fly.dev/mcp'
            or env.get('CRM_PLATFORM_JOB_OPERATIONS')):
        raise RuntimeError('Refusing unexpected staging revision or configuration')
    operator = make_url(operator_url.replace('postgres://', 'postgresql://', 1))
    runtime = make_url(env.get('DATABASE_URL', '').replace('postgres://', 'postgresql://', 1))
    for url, user in ((operator, DATABASE), (runtime, ROLE)):
        if (url.get_backend_name() != 'postgresql' or url.host not in HOSTS
                or url.database != DATABASE or url.username != user
                or set(url.query) - {'sslmode'}
                or url.query.get('sslmode', 'require') not in ('disable', 'allow', 'prefer', 'require', 'verify-ca', 'verify-full')
                or url.port not in (None, 5432)):
            raise RuntimeError('Refusing unexpected staging database connection')
    return operator.set(host='pathsixsolutions-db-staging.internal'), runtime


def snapshot(engine, tables, schema='public'):
    """Bounded, consistent hashes; record contents and credentials are never output."""
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as c, c.begin():
        c.execute(text('SET TRANSACTION READ ONLY'))
        c.execute(text("SET LOCAL statement_timeout='15s'"))
        q = c.dialect.identifier_preparer.quote
        result = {}
        for name in sorted(tables):
            table = f'{q(schema)}.{q(name)}'
            count = c.execute(text(f'SELECT count(*) FROM {table}')).scalar_one()
            if count > 10000:
                raise RuntimeError('Staging data exceeds bounded verification size')
            rows = sorted(json.dumps(dict(row), sort_keys=True, default=str)
                          for row in c.execute(text(f'SELECT * FROM {table}')).mappings())
            result[name] = {'count': count, 'digest': hashlib.sha256('\n'.join(rows).encode()).hexdigest()}
        result['sequences'] = [list(row) for row in c.execute(text(
            'SELECT sequencename,last_value FROM pg_sequences WHERE schemaname=:schema ORDER BY sequencename'
        ), {'schema': schema})]
        result['test_schemas'] = list(c.execute(text(
            "SELECT nspname FROM pg_namespace WHERE nspname ~ '^security_test_[0-9a-f]{32}$' ORDER BY nspname"
        )).scalars())
        if 'alembic_version' in tables:
            result['migration_heads'] = list(c.execute(text(
                f'SELECT version_num FROM {q(schema)}.alembic_version ORDER BY version_num')).scalars())
        return result


def attest(engine):
    from ops.isolation_contract import verify_isolation_contract
    with engine.connect() as c, c.begin():
        c.execute(text('SET TRANSACTION READ ONLY'))
        c.execute(text('SET LOCAL search_path=public'))
        c.execute(text("SET LOCAL statement_timeout='15s'"))
        if c.execute(text('SELECT current_user')).scalar_one() != ROLE:
            raise RuntimeError('Unexpected runtime role')
        result = verify_isolation_contract(c, 'public', ROLE)
        for privilege in ('SELECT', 'INSERT', 'DELETE', 'TRUNCATE', 'UPDATE'):
            allowed = c.execute(text("SELECT has_table_privilege(:role,'public.ai_write_actions',:privilege)"),
                                {'role': ROLE, 'privilege': privilege}).scalar_one()
            if allowed != (privilege in ('SELECT', 'INSERT')):
                raise RuntimeError('Action table privilege drift')
        columns = c.execute(text("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='ai_write_actions'")).scalars().all()
        for column in columns:
            allowed = c.execute(text("SELECT has_column_privilege(:role,'public.ai_write_actions',:column,'UPDATE')"),
                                {'role': ROLE, 'column': column}).scalar_one()
            if allowed != (column in ('status', 'result_id', 'decided_at')):
                raise RuntimeError('Action column privilege drift')
        trigger = c.execute(text("SELECT tgenabled,tgtype,tgfoid='public.crm_action_immutable()'::regprocedure AS expected_function,pg_get_triggerdef(oid) AS definition,pg_get_functiondef(tgfoid) AS function FROM pg_trigger WHERE tgrelid='public.ai_write_actions'::regclass AND tgname='crm_action_immutable' AND NOT tgisinternal")).mappings().one_or_none()
        if trigger is None or trigger['tgenabled'] != 'O' or trigger['tgtype'] != 23 or not trigger['expected_function']:
            raise RuntimeError('Immutable action trigger is missing or disabled')
        result['action_trigger_digest'] = hashlib.sha256(
            (trigger['definition'] + '\n' + trigger['function']).encode()).hexdigest()
        return result


class Results:
    def __init__(self):
        self.passed = 0
        self.skipped = 0
        self.failed = set()

    def pytest_runtest_logreport(self, report):
        if report.failed:
            self.failed.add(report.nodeid)
        if report.skipped:
            self.skipped += 1
        if report.when == 'call' and report.passed:
            self.passed += 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--test-root', required=True, type=Path)
    args = parser.parse_args()
    operator, runtime = require_environment(args.revision, sys.stdin.readline().strip(), os.environ)
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    tests = args.test_root.resolve()
    if not all((tests / name).is_file() for name in (*TESTS, 'conftest.py')):
        raise RuntimeError('Upload the complete reviewed tests directory first')
    # Fail before any tests if PostgreSQL/pytest dependencies are missing.
    import pytest
    from ops.isolation_contract import TABLES
    admin = create_engine(operator, hide_parameters=True, connect_args={'connect_timeout': 10})
    restricted = create_engine(runtime, hide_parameters=True, connect_args={'connect_timeout': 10})
    try:
        contract = attest(restricted)
        tables = (*TABLES, 'roles', 'backups', 'backup_restores', 'ai_clients', 'alembic_version')
        before = snapshot(admin, tables)
        if before['migration_heads'] != ['mcp_lead_creation']:
            raise RuntimeError('Lead migration has not been applied')
        if before['test_schemas']:
            raise RuntimeError('Existing disposable test schemas require review')
        os.environ.update(SECURITY_TEST_DATABASE_URL=operator.render_as_string(hide_password=False),
                          SECURITY_TEST_ROLE=ROLE, SENTRY_DSN='')
        report = Results()
        # No traceback/SQL/connection strings from failed integration checks escape.
        with tempfile.TemporaryDirectory(prefix='crm-lead-verification-') as work:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = pytest.main([*(str(tests / name) for name in TESTS), '-q', '-x',
                    '--tb=no', '--disable-warnings', '-p', 'no:cacheprovider',
                    '--basetemp=' + str(Path(work) / 'pytest')], plugins=[report])
        after = snapshot(admin, tables)
        unchanged = before == after
        attested = attest(restricted) == contract
        success = code == 0 and report.passed > 0 and report.skipped == 0 and unchanged and attested
        print(json.dumps({'revision': args.revision, 'passed': report.passed,
            'skipped': report.skipped, 'failed_tests': sorted(report.failed), 'pytest_exit': int(code),
            'public_data_unchanged': unchanged, 'test_schemas_clean': not after['test_schemas'],
            'contract': contract, 'contract_unchanged': attested, 'success': success}))
        return 0 if success else 1
    finally:
        admin.dispose()
        restricted.dispose()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({'success': False, 'exception': type(error).__name__}), file=sys.stderr)
        raise SystemExit(1) from None
