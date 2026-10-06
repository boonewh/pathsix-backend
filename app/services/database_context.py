"""Trusted identity for one SQLAlchemy session; PostgreSQL settings are transaction-local."""
import os
from sqlalchemy import text
from sqlalchemy.orm import Session
from quart import has_request_context, request
from app.services.principal import Principal


def enabled():
    return os.getenv('CRM_RLS_ENABLED') == '1'


def auth_lookup(session, *, user_id=None, email=None, password_reset=False):
    # Lightweight test doubles do not execute SQL or need a PostgreSQL context.
    if isinstance(session, Session):
        if ((user_id is None) == (email is None)
                or (user_id is not None and (type(user_id) is not int or user_id < 1))
                or (email is not None and (not isinstance(email, str) or not email))
                or type(password_reset) is not bool):
            raise ValueError('Exactly one valid authentication identity is required')
        lookup = (user_id, email, password_reset)
        previous = session.info.get('auth_lookup')
        if (session.info.get('principal') is not None
                or (has_request_context() and getattr(request, 'principal', None) is not None)
                or (previous is not None and previous != lookup)
                or (previous is None and session.in_transaction())):
            raise ValueError('Authentication lookup requires a fresh, fixed-identity session')
        session.info['auth_lookup'] = lookup


def bind_principal(session, principal, *, _transaction_start=False):
    if not isinstance(principal, Principal):
        raise TypeError('An authenticated principal is required')
    if session.info.get('auth_lookup') is not None:
        raise ValueError('Authentication and tenant operations require separate sessions')
    previous = session.info.get('principal')
    if previous is not None and previous != principal:
        raise ValueError('A database session cannot change authenticated identity')
    if has_request_context() and getattr(request, 'principal', None) != principal:
        raise ValueError('Service identity must match authenticated request')
    if previous is None:
        # ORM identity maps can return rows without issuing SQL (and therefore
        # without invoking RLS). Never adopt already-loaded foreign tenant state.
        for state in session.identity_map.all_states():
            tenant = state.dict.get('tenant_id')
            table = state.mapper.local_table.name
            if ((tenant is not None and tenant != principal.tenant_id)
                    or (table == 'tenants' and state.dict.get('id') != principal.tenant_id)
                    or (table in ('user_preferences', 'ai_connections', 'oauth_credentials', 'ai_tool_audits', 'ai_write_actions')
                        and state.dict.get('user_id') != principal.user_id)):
                raise ValueError('A tenant session cannot adopt foreign cached records')
    session.info['principal'] = principal
    if enabled() and session.in_transaction() and previous is None and not _transaction_start:
        apply_context(session, session.connection())


def validate_session_context(session, *, transaction_start=False):
    principal = session.info.get('principal')
    if has_request_context():
        current = getattr(request, 'principal', None)
        if principal is not None and current is None:
            raise ValueError('A tenant session cannot serve an unauthenticated request')
        if current is not None:
            if principal is None:
                bind_principal(session, current, _transaction_start=transaction_start)
                principal = current
            elif current != principal:
                raise ValueError('A database session cannot change authenticated identity')
    return principal


def apply_context(session, connection):
    principal = validate_session_context(session, transaction_start=True)
    if not enabled() or connection.dialect.name != 'postgresql':
        return
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
    for key, value in (('crm.tenant_id', tenant), ('crm.user_id', str(principal.user_id) if principal else ''),
                       ('crm.auth_user_id', auth_user),
                       ('crm.auth_tenant_id', auth_tenant), ('crm.reset_user_id', reset_user)):
        connection.execute(text('SELECT set_config(:key, :value, true)'), {'key': key, 'value': value})
