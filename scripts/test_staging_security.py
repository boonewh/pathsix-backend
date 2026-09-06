"""Run the regression suite in disposable schemas on the staging database only.

Run after installing requirements-test.txt in the staging machine's /tmp.
Never changes the public schema or sends email.
"""
import os
import sys
from pathlib import Path
from urllib.parse import urlparse


def main():
    if os.getenv("FLY_APP_NAME") != "pathsixsolutions-backend-staging":
        raise SystemExit("Refusing: this script only runs in the staging Fly app")
    url = os.environ["DATABASE_URL"]
    if urlparse(url).hostname not in {
        "pathsixsolutions-db-staging.flycast", "pathsixsolutions-db-staging.internal"
    }:
        raise SystemExit("Refusing: unexpected database host")
    os.environ["SECURITY_TEST_DATABASE_URL"] = url.replace("postgres://", "postgresql://", 1)
    os.environ["SENTRY_DSN"] = ""
    os.chdir(Path(__file__).resolve().parents[1])
    sys.path.insert(0, str(Path.cwd()))
    import pytest
    raise SystemExit(pytest.main(["-q", "tests", "--disable-warnings", "--basetemp=/tmp/crm-security-tests"]))


if __name__ == "__main__":
    main()
