"""Bounded retention for AI connection records. Dry run unless --apply.

Run on the app's own Fly machine with the administrator URL on stdin (never as
an argument), for example from the operator workstation:

    <admin-url helper> | flyctl ssh console --app <app> -C "python scripts/prune_ai_records.py [--apply]"

Rules (times are naive UTC, as stored):
1. A connection that ended (revoked, or its active_until passed) more than
   ENDED_DAYS ago is deleted with its credentials, tool audits and action
   receipts. Records it created stay in the CRM.
2. Credentials that expired more than EXPIRED_CREDENTIAL_DAYS ago are deleted.

Live connections keep their audits and receipts: request keys must stay unique
for replay protection while a connection can still send requests. A connection
lives at most 365 days, so nothing is kept longer than about 365 + ENDED_DAYS.
The administrator role must bypass row security (superuser or BYPASSRLS),
otherwise forced row security would make deletes silently remove nothing.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ENDED_DAYS = 90
EXPIRED_CREDENTIAL_DAYS = 30
APPS = {'pathsixsolutions-backend-staging', 'pathsixsolutions-backend'}

ENDED = '''SELECT id FROM ai_connections
            WHERE LEAST(COALESCE(revoked_at, active_until), active_until) < :ended_before'''


def prune(connection, now, apply):
    ended_before = now - timedelta(days=ENDED_DAYS)
    expired_before = now - timedelta(days=EXPIRED_CREDENTIAL_DAYS)
    params = {'ended_before': ended_before, 'expired_before': expired_before}
    ended = f'connection_id IN ({ENDED})'
    plan = [
        ('credentials of ended connections', f'oauth_credentials WHERE {ended}'),
        ('tool audits of ended connections', f'ai_tool_audits WHERE {ended}'),
        ('action receipts of ended connections', f'ai_write_actions WHERE {ended}'),
        ('ended connections', f'ai_connections WHERE id IN ({ENDED})'),
        ('expired credentials', 'oauth_credentials WHERE expires_at < :expired_before'),
    ]
    counts = {}
    for label, target in plan:
        if apply:
            counts[label] = connection.execute(text(f'DELETE FROM {target}'), params).rowcount
        else:
            counts[label] = connection.execute(text(f'SELECT count(*) FROM {target}'), params).scalar()
    return counts


def require_target(admin_url):
    if os.getenv('FLY_APP_NAME') not in APPS:
        raise SystemExit('Refusing to run outside a PathSix backend Fly app.')
    runtime = make_url(os.getenv('DATABASE_URL', '').replace('postgres://', 'postgresql://', 1))
    admin = make_url(admin_url)
    if (admin.host, admin.port, admin.database) != (runtime.host, runtime.port, runtime.database):
        raise SystemExit("Refusing an administrator URL for a different database than this app's.")
    return admin


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    admin = require_target(sys.stdin.readline().strip().replace('postgres://', 'postgresql://', 1))
    engine = create_engine(admin, hide_parameters=True)
    with engine.begin() as connection:
        connection.execute(text('SET LOCAL search_path=public'))
        connection.execute(text("SET LOCAL lock_timeout='5s'"))
        connection.execute(text("SET LOCAL statement_timeout='60s'"))
        role = connection.execute(text(
            'SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user')).scalar()
        if not role:
            raise SystemExit('The administrator role must bypass row security; nothing was deleted.')
        counts = prune(connection, datetime.now(timezone.utc).replace(tzinfo=None), args.apply)
    print(json.dumps({'app': os.getenv('FLY_APP_NAME'), 'applied': args.apply,
                      'ended_days': ENDED_DAYS, 'expired_credential_days': EXPIRED_CREDENTIAL_DAYS,
                      'rows': counts}))


if __name__ == '__main__':
    main()
