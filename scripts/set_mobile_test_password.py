"""Set the password of a synthetic user on the MOBILE environment only (mobile Phase 1).

The iPhone app's on-device acceptance needs a sign-in whose password the owner
knows, without a password ever appearing in a prompt, an argument, a log or a
report. Run on the mobile app's own Fly machine; the password arrives on stdin:

    <password source> | flyctl ssh console --app pathsixsolutions-backend-mobile \\
        -C "python scripts/set_mobile_test_password.py --email <synthetic user email>"

Refuses outside pathsixsolutions-backend-mobile. Uses the app's own restricted
DATABASE_URL under the user's tenant (row security applies). The new hash also
changes the device credential marker, so that user's existing phones sign out.
Prints only the user id and whether the password was set, never the password.
"""
import argparse
import os
import sys

APP = 'pathsixsolutions-backend-mobile'
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
    updated = connection.execute(text('UPDATE users SET password_hash=:h WHERE id=:u AND tenant_id=:t AND is_active'),
                                 {'h': password_hash, 'u': row.user_id, 't': row.tenant_id}).rowcount
    return row.user_id if updated == 1 else None


def main(argv=None, stdin=sys.stdin):
    parser = argparse.ArgumentParser()
    parser.add_argument('--email', required=True)
    args = parser.parse_args(argv)
    if os.getenv('FLY_APP_NAME') != APP:
        raise SystemExit(f'Refusing to run outside {APP}.')
    password = read_password(stdin)
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
    from sqlalchemy import create_engine
    from app.utils.auth_utils import hash_password
    engine = create_engine(os.environ['DATABASE_URL'].replace('postgres://', 'postgresql://', 1), hide_parameters=True)
    try:
        with engine.begin() as connection:
            user_id = set_password(connection, args.email.strip().lower(), hash_password(password))
    finally:
        engine.dispose()
    if user_id is None:
        raise SystemExit('No active user with that email; nothing was changed.')
    print(f'Password set for user {user_id}. Their existing phones are signed out.')


if __name__ == '__main__':
    main()
