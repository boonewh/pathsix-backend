"""Run behavioral tenant-boundary checks instead of regex query heuristics.

Install requirements-test.txt first. Set SECURITY_TEST_DATABASE_URL to an isolated
PostgreSQL test database to exercise PostgreSQL; otherwise tests use temp SQLite.
This suite covers known REST gaps; it does not certify complete MCP readiness.
"""
from pathlib import Path
import subprocess
import sys

if __name__ == "__main__":
    raise SystemExit(subprocess.call(
        [sys.executable, "-m", "pytest", "-q", "tests/test_security_boundaries.py"],
        cwd=Path(__file__).resolve().parent,
    ))
