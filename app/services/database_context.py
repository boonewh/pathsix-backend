"""Trusted identity for one SQLAlchemy session; PostgreSQL settings are transaction-local."""
import os
from sqlalchemy import text
from sqlalchemy.orm import Session
from quart import has_request_context, request


def enabled():
    return os.getenv('CRM_RLS_ENABLED') == '1'


def auth_lookup(session, *, user_id=None, email=None, password_reset=False):
    # Lightweight test doubles do not execute SQL or need a PostgreSQL context.
    if isinstance(session, Session):
        session.info['auth_lookup'] = (user_id, email, password_reset)


def bind_principal(session, principal):
    previous = session.info.get('principal')
    if previous is not None and previous != principal:
        raise ValueError('A database session cannot change authenticated identity')
    if has_request_context() and getattr(request, 'principal', principal) != principal:
        raise ValueError('Service identity must match authenticated request')
    session.info['principal'] = principal
    if enabled() and session.in_transaction() and previous is None:
        apply_context(session, session.connection())


def apply_context(session, connection):
    if not enabled() or connection.dialect.name != 'postgresql':
        return
    principal = session.info.get('principal')
    if principal is None and has_request_context():
        principal = getattr(request, 'principal', None)
    tenant = str(principal.tenant_id) if principal else ''
    auth_user = auth_tenant = reset_user = ''
    lookup = session.info.get('auth_lookup')
    if lookup and principal is None:
        user_id, email, password_reset = lookup
        row = connection.execute(text('SELECT * FROM crm_auth_identity(:user_id, :email)'),
                                 {'user_id': user_id, 'email': email}).first()
        if row:
            auth_user, auth_tenant = str(row[0]), str(row[1])
            if password_reset:
                reset_user = auth_user
    # Set every value, including empty values, on every new transaction. SET LOCAL
    # clears at commit/rollback, so pooled connections cannot carry tenant identity.
    for key, value in (('crm.tenant_id', tenant), ('crm.auth_user_id', auth_user),
                       ('crm.auth_tenant_id', auth_tenant), ('crm.reset_user_id', reset_user)):
        connection.execute(text('SELECT set_config(:key, :value, true)'), {'key': key, 'value': value})
