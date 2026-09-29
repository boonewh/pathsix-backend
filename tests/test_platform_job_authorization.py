"""Platform authorization checks never run a real backup, restore or deletion."""
import asyncio
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from quart import Quart
from sqlalchemy.exc import OperationalError
from test_security_boundaries import crm
from app.utils import auth_utils
from app.workers import authorization, backup_jobs, restore_jobs
from scripts import run_worker, run_scheduled_backup, cleanup_backups


@pytest.mark.parametrize('entry', ['backup','restore','cleanup','scheduled','worker','cleanup_script'])
def test_platform_entries_deny_before_side_effects(monkeypatch, entry):
    monkeypatch.delenv('CRM_PLATFORM_JOB_OPERATIONS',raising=False)
    touched=Mock(side_effect=AssertionError('Side effect before authorization'))
    for module in (backup_jobs,restore_jobs,run_scheduled_backup):
        monkeypatch.setattr(module,'SessionLocal',touched)
    monkeypatch.setattr(backup_jobs,'get_backup_storage',touched)
    monkeypatch.setattr(restore_jobs,'get_backup_storage',touched)
    monkeypatch.setattr(run_worker,'Worker',touched)
    invoke={'backup':lambda:backup_jobs.run_backup_job(1),
            'restore':lambda:restore_jobs.run_restore_job(1),
            'cleanup':backup_jobs.cleanup_old_backups,
            'scheduled':run_scheduled_backup.main,
            'worker':run_worker.main,'cleanup_script':cleanup_backups.main}[entry]
    if entry=='cleanup_script':
        assert invoke()==1
    else:
        with pytest.raises(PermissionError): invoke()
    touched.assert_not_called()


@pytest.mark.parametrize('allowed', ['', 'backup,unknown', '*', 'restore-only'])
def test_invalid_allowlist_denies_before_database(monkeypatch, allowed):
    monkeypatch.setenv('CRM_PLATFORM_JOB_OPERATIONS',allowed)
    probe=Mock(side_effect=AssertionError('Unexpected database access'))
    monkeypatch.setattr(authorization,'_require_platform_database',probe)
    with pytest.raises(PermissionError): authorization.require_platform_operation('backup')
    probe.assert_not_called()


def test_allowlist_separates_backup_restore_and_cleanup(monkeypatch):
    monkeypatch.setenv('CRM_PLATFORM_JOB_OPERATIONS','backup, cleanup')
    probe=Mock()
    monkeypatch.setattr(authorization,'_require_platform_database',probe)
    authorization.require_platform_operation('backup')
    authorization.require_platform_operation('cleanup')
    authorization.require_platform_worker()
    assert probe.call_count==3
    with pytest.raises(PermissionError): authorization.require_platform_operation('restore')
    assert probe.call_count==3


def test_http_context_cannot_authorize_platform_jobs_even_when_enabled(monkeypatch):
    monkeypatch.setenv('CRM_PLATFORM_JOB_OPERATIONS','backup,restore,cleanup')
    probe=Mock(side_effect=AssertionError('Unexpected database access'))
    monkeypatch.setattr(authorization,'_require_platform_database',probe)
    app=Quart(__name__)
    async def exercise():
        async with app.test_request_context('/'):
            for operation in authorization.OPERATIONS:
                with pytest.raises(PermissionError): authorization.require_platform_operation(operation)
            with pytest.raises(PermissionError): authorization.require_platform_worker()
    asyncio.run(exercise())
    probe.assert_not_called()


@pytest.mark.parametrize('privileged,identities,accepted', [
    (False,('', '', '', ''),False), (True,('1','','',''),False),
    (True,('','1','',''),False), (True,('','','1',''),False),
    (True,('','','','1'),False), (True,(None,'',None,''),True)])
def test_database_privilege_and_absent_identity_are_both_required(monkeypatch, privileged, identities, accepted):
    connection=Mock()
    connection.execute.side_effect=[SimpleNamespace(scalar_one=lambda:privileged),SimpleNamespace(one=lambda:identities)]
    @contextmanager
    def connect(): yield connection
    monkeypatch.setattr(authorization,'engine',SimpleNamespace(dialect=SimpleNamespace(name='postgresql'),connect=connect))
    monkeypatch.setenv('CRM_PLATFORM_JOB_OPERATIONS','backup')
    if accepted:
        authorization.require_platform_operation('backup')
    else:
        with pytest.raises(PermissionError): authorization.require_platform_operation('backup')


def test_database_failure_does_not_disclose_credentials(monkeypatch):
    def connect():
        raise OperationalError('private sql',{},Exception('private connection'))
    monkeypatch.setattr(authorization,'engine',SimpleNamespace(dialect=SimpleNamespace(name='postgresql'),connect=connect))
    monkeypatch.setenv('CRM_PLATFORM_JOB_OPERATIONS','backup')
    with pytest.raises(PermissionError,match='Unable to verify') as error:
        authorization.require_platform_operation('backup')
    assert 'private' not in str(error.value)


def test_actual_runtime_connection_cannot_become_platform_worker(crm, monkeypatch):
    runtime=auth_utils.SessionLocal
    @contextmanager
    def connect():
        with runtime() as db: yield db.connection()
    with runtime() as db:
        dialect=db.bind.dialect
    monkeypatch.setattr(authorization,'engine',SimpleNamespace(dialect=dialect,connect=connect))
    monkeypatch.setenv('CRM_PLATFORM_JOB_OPERATIONS','backup,restore,cleanup')
    if dialect.name=='postgresql':
        import os
        if not os.getenv('SECURITY_TEST_ROLE'):
            pytest.skip('Restricted PostgreSQL runtime role required')
    for operation in authorization.OPERATIONS:
        with pytest.raises(PermissionError): authorization.require_platform_operation(operation)
