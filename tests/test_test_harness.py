"""Regression coverage for interrupted fixture setup and safe diagnostic output."""
import json
import os
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

import test_security_boundaries as boundaries
from conftest import phase_diagnostic


def test_setup_failure_finalizer_removes_its_schema(tmp_path, monkeypatch):
    finalizers = []
    schema = 'security_test_' + boundaries.uuid.uuid4().hex
    monkeypatch.setattr(boundaries.uuid, 'uuid4', lambda: SimpleNamespace(hex=schema.removeprefix('security_test_')))
    def fail(*args, **kwargs):
        raise RuntimeError('Synthetic setup interruption after schema creation')
    monkeypatch.setattr(boundaries.Base.metadata, 'create_all', fail)
    try:
        with pytest.raises(RuntimeError, match='Synthetic setup interruption'):
            boundaries.crm.__wrapped__(tmp_path, monkeypatch, SimpleNamespace(addfinalizer=finalizers.append))
        assert len(finalizers) == 1
    finally:
        for finalizer in reversed(finalizers):
            finalizer()
    url = os.getenv('SECURITY_TEST_DATABASE_URL')
    if url:
        engine = create_engine(url, hide_parameters=True)
        try:
            with engine.connect() as connection:
                assert connection.execute(text('SELECT count(*) FROM pg_namespace WHERE nspname=:schema'), {'schema': schema}).scalar_one() == 0
        finally:
            engine.dispose()


def test_phase_diagnostic_omits_private_exception_details():
    error = OperationalError('PRIVATE SQL', {'password': 'PRIVATE PASSWORD'}, Exception('PRIVATE URL'), connection_invalidated=True)
    result = phase_diagnostic('safe_test', 'setup', 'failed', error)
    assert result['connection_invalidated'] is True
    assert result['exception'] == 'OperationalError'
    assert 'PRIVATE' not in json.dumps(result)
    assert result['utc'] and result['phase'] == 'setup'
