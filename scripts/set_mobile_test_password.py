"""Set the password of a synthetic user on the MOBILE environment only (mobile Phase 1).

The iPhone app's on-device acceptance needs a sign-in whose password the owner
knows, without a password ever appearing in a prompt, an argument, a log or a
report. Run on the mobile app's own Fly machine; the password arrives on stdin:

    <password source> | flyctl ssh console --app pathsixsolutions-backend-mobile \\
        -C "python scripts/set_mobile_test_password.py --email <synthetic user email>"

Refuses outside pathsixsolutions-backend-mobile, and refuses any DATABASE_URL
that is not the mobile database reached as its restricted runtime login (host,
database and user match scripts/database_targets.py; no query overrides). Once
connected it re-verifies the database, the login, that the login cannot bypass
row security, and that row security is on for users, then updates under the
user's tenant only. An inactive user or tenant is refused. The new hash also
changes the device credential marker, so that user's existing phones sign out.
Prints only the user id, never the password or its hash; database errors are
reported with a fixed message. Residual: like any failed statement, a failed
UPDATE may appear in PostgreSQL's own error log with its parameters (the bcrypt
hash, not the password).
"""
import argparse
import os
import sys

APP = 'pathsixsolutions-backend-mobile'
ALLOWED_QUERY = {'sslmode'}


class Refused(SystemExit):
    pass


def mobile_target():
    sys.path.insert(0, os.path.dirname(__file__))
    from database_targets import target
    return target('mobile')


def checked_runtime_url(raw):
    """The app's DATABASE_URL, only if it is the mobile database as the runtime login."""
    from sqlalchemy.engine import make_url
    spec = mobile_target()
    url = make_url(raw.strip().replace('postgres://', 'postgresql://', 1))
    if set(url.query) - ALLOWED_QUERY:
        raise Refused('Refusing a database URL with connection overrides.')
    if url.host not in spec['hosts'] or url.database != spec['database'] or url.username != spec['runtime_role']:
        raise Refused('Refusing: DATABASE_URL is not the mobile database as its restricted runtime login.')
    return url


def verify_connection(connection):
    from sqlalchemy import text
    spec = mobile_target()
    database, user, bypass, rls = connection.execute(text(
        "SELECT current_database(), current_user, "
        "(SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user), "
        "(SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
        " WHERE oid = to_regclass('users'))")).one()
    if database != spec['database'] or user != spec['runtime_role'] or bypass or not rls:
        raise Refused('Refusing: the connection is not the restricted mobile runtime login with row security.')
MIN_LENGTH = 12
MAX_BYTES = 72  # bcrypt's limit, as UserService enforces


def read_password(stream):
    password = stream.readline().rstrip('\r\n')
    if len(password) < MIN_LENGTH or len(password.encode('utf-8')) > MAX_BYTES:
        raise SystemExit(f'The password must be at least {MIN_LENGTH} characters and at most {MAX_BYTES} bytes.')
    return password


def set_password(connection, email, password_hash):
    from sqlalchemy import text
    # Bootstrap the user's tenant through the narrow identity function, then
    # update under that tenant's row security.
    row = connection.execute(text('SELECT user_id, tenant_id FROM crm_auth_identity(NULL, :email)'),
                             {'email': email}).first()
    if row is None:
        return None
    connection.execute(text("SELECT set_config('crm.tenant_id', :t, true)"), {'t': str(row.tenant_id)})
    # Recheck everything in the write itself: the same id, tenant and email, an
    # active user, and an active tenant (device sign-in refuses either inactive).
    updated = connection.execute(text(
        'UPDATE users u SET password_hash = :h FROM tenants t '
        'WHERE u.id = :u AND u.tenant_id = :t AND lower(u.email) = :e AND u.is_active '
        'AND t.id = u.tenant_id AND t.is_active'),
        {'h': password_hash, 'u': row.user_id, 't': row.tenant_id, 'e': email}).rowcount
    return row.user_id if updated == 1 else None


def main(argv=None, stdin=sys.stdin):
    parser = argparse.ArgumentParser()
    parser.add_argument('--email', required=True)
    args = parser.parse_args(argv)
    if os.getenv('FLY_APP_NAME') != APP:
        raise SystemExit(f'Refusing to run outside {APP}.')
    url = checked_runtime_url(os.getenv('DATABASE_URL', ''))
    password = read_password(stdin)
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
    from sqlalchemy import create_engine
    from sqlalchemy.exc import SQLAlchemyError
    from app.utils.auth_utils import hash_password
    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.begin() as connection:
            verify_connection(connection)
            user_id = set_password(connection, args.email.strip().lower(), hash_password(password))
    except SQLAlchemyError:
        # Driver errors can carry statement text or row values: report none of it.
        raise SystemExit('Database error; nothing was changed.') from None
    finally:
        engine.dispose()
    if user_id is None:
        raise SystemExit('No active user with that email; nothing was changed.')
    print(f'Password set for user {user_id}. Their existing phones are signed out.')


if __name__ == '__main__':
    main()
