"""Opt-in, credential-safe phase diagnostics for serial PostgreSQL checks."""
import json
import os
from datetime import datetime, timezone

import pytest


def phase_diagnostic(nodeid, phase, outcome, error=None):
    result = {'utc': datetime.now(timezone.utc).isoformat(), 'test': nodeid,
              'phase': phase, 'outcome': outcome}
    if error is not None:
        result['exception'] = type(error).__name__
        original = getattr(error, 'orig', None)
        code = getattr(original, 'pgcode', None)
        if isinstance(code, str) and len(code) == 5 and code.isalnum():
            result['sqlstate'] = code
        result['connection_invalidated'] = bool(getattr(error, 'connection_invalidated', False))
    # Never emit exception messages, SQL, parameters or connection strings.
    return result


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if os.getenv('CRM_TEST_DIAGNOSTICS') == '1':
        terminal = item.config.pluginmanager.getplugin('terminalreporter')
        if terminal:
            terminal.write_line(json.dumps(phase_diagnostic(
                item.nodeid, report.when, report.outcome,
                call.excinfo.value if call.excinfo else None)))


# Test tiers. CI always runs everything. Locally, pick a tier with -m:
#   core       always on: the AI connector (MCP, OAuth, reviewed actions)
#   isolation  cross-cutting tenant isolation and web authorization; run when
#              shared services, access rules, models or routes change
#   crm        web CRM services; run when CRM routes or services change
#   migration  database migration rehearsals; run when a migration changes and
#              before a production rollout
#   ops        one-off operational scripts; run when those scripts change
CORE_FILES = {
    'test_ai_action_kinds.py', 'test_ai_connections.py', 'test_mcp_client_registration.py',
    'test_mcp_connection_discovery.py', 'test_mcp_inline_review.py', 'test_mcp_lead_creation.py', 'test_mcp_reads.py',
    'test_mcp_record_creation.py', 'test_mcp_record_reads.py', 'test_mcp_transport_diagnostics.py', 'test_oauth.py',
    'test_rate_limiter.py',
}
ISOLATION_FILES = {'test_isolation_proof.py', 'test_security_boundaries.py'}
OPS_FILES = {'test_staging_lead_verification.py', 'test_upgrade_security.py', 'test_test_harness.py'}


def tier(filename):
    if filename.endswith('_migration.py'):
        return 'migration'
    if filename in CORE_FILES:
        return 'core'
    if filename in ISOLATION_FILES:
        return 'isolation'
    if filename in OPS_FILES:
        return 'ops'
    return 'crm'


def pytest_configure(config):
    for name, purpose in (('core', 'always-on AI connector checks'), ('isolation', 'tenant isolation and web authorization'),
                          ('crm', 'web CRM services'), ('migration', 'migration rehearsals'),
                          ('ops', 'operational scripts')):
        config.addinivalue_line('markers', f'{name}: {purpose}')


def pytest_collection_modifyitems(config, items):
    for item in items:
        item.add_marker(getattr(pytest.mark, tier(item.path.name)))
