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
