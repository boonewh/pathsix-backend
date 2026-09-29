"""Fail-closed process boundary for whole-database platform operations.

This is operator configuration, not a tenant role or a delegated user grant.
Do not set the allowlist or privileged credentials on a web/MCP deployment.
"""
import os

from quart import has_request_context
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from app.database import engine

OPERATIONS = frozenset({'backup', 'restore', 'cleanup'})


def _allowed_operations():
    allowed = {part.strip() for part in os.getenv('CRM_PLATFORM_JOB_OPERATIONS', '').split(',') if part.strip()}
    if has_request_context() or not allowed or not allowed <= OPERATIONS:
        raise PermissionError('Platform jobs require an explicitly configured worker process')
    return allowed


def _require_platform_database():
    if engine.dialect.name != 'postgresql':
        raise PermissionError('Platform jobs require a privileged PostgreSQL worker connection')
    try:
        with engine.connect() as connection:
            privileged = connection.execute(text(
                'SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user'
            )).scalar_one()
            identities = connection.execute(text(
                "SELECT current_setting('crm.tenant_id', true), "
                "current_setting('crm.auth_user_id', true), "
                "current_setting('crm.auth_tenant_id', true), "
                "current_setting('crm.reset_user_id', true)"
            )).one()
            if not privileged or any(identities):
                raise PermissionError('Platform jobs require a privileged connection without tenant identity')
    except SQLAlchemyError:
        raise PermissionError('Unable to verify platform worker database authorization') from None


def require_platform_operation(operation):
    if operation not in _allowed_operations():
        raise PermissionError('Platform operation is not enabled for this worker')
    _require_platform_database()


def require_platform_worker():
    _allowed_operations()
    _require_platform_database()
