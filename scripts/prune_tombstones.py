"""Retention for tombstones (deleted contacts, accounts, interactions) and sync_purges.

Dry run unless --apply.

Run on the app's own Fly machine with the administrator URL on stdin (never as
an argument), for example from the operator workstation:

    <admin-url helper> | flyctl ssh console --app <app> -C "python scripts/prune_tombstones.py [--apply]"

A tombstone tells offline devices that a record was deleted, so it is kept for
RETENTION_DAYS after deletion (times are naive UTC, as stored) and then removed.
A device offline for longer must resynchronize from scratch, which the change
feed (mobile Phase 0 item 4) enforces by refusing cursors older than 89 days. Standalone on purpose: importing the
app would open its runtime database engine. Keep RETENTION_DAYS equal to
app/utils/tombstones.py. Live records are never touched. Nothing references these three
tables, so deleting a tombstone cannot cascade.

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

RETENTION_DAYS = 90
# Same table order as PurgeService (interactions, then contacts, then accounts),
# so a concurrent purge and prune do not take these tables in opposite orders.
TABLES = ('interactions', 'contacts', 'accounts')
# Written by the change feed's delete trigger; same retention as tombstones.
PURGES = 'sync_purges'
# Idempotency receipts (mobile_idempotent_writes); a key reused after this writes again.
RECEIPTS = 'write_receipts'
# Mobile only until tombstones are approved for staging or production.
APPS = {'pathsixsolutions-backend-mobile'}
# libpq lets query parameters such as host, port or dbname override the URL's own
# fields, which would defeat the target check below. Only these are accepted.
ALLOWED_QUERY = {'sslmode'}


def prune(connection, now, apply):
    params = {'before': now - timedelta(days=RETENTION_DAYS)}
    counts = {}
    for table in TABLES:
        target = f'{table} WHERE deleted_at IS NOT NULL AND deleted_at < :before'
        if apply:
            # Lock only rows nobody else holds, then delete exactly those. Rows a
            # concurrent purge has locked are skipped (it is deleting them anyway,
            # or a later run collects them), so the two cannot deadlock.
            counts[table] = connection.execute(text(
                f'DELETE FROM {table} WHERE id IN '
                f'(SELECT id FROM {target} ORDER BY id FOR UPDATE SKIP LOCKED)'), params).rowcount
        else:
            counts[table] = connection.execute(text(f'SELECT count(*) FROM {target}'), params).scalar()
    # Permanent-delete records for the change feed (mobile_change_feed). The delete
    # trigger records the tombstones removed above too; those age out in a later run.
    target = f'{PURGES} WHERE purged_at < :before'
    if apply:
        counts[PURGES] = connection.execute(text(f'DELETE FROM {target}'), params).rowcount
    else:
        counts[PURGES] = connection.execute(text(f'SELECT count(*) FROM {target}'), params).scalar()
    target = f'{RECEIPTS} WHERE created_at < :before'
    if apply:
        counts[RECEIPTS] = connection.execute(text(f'DELETE FROM {target}'), params).rowcount
    else:
        counts[RECEIPTS] = connection.execute(text(f'SELECT count(*) FROM {target}'), params).scalar()
    return counts


def require_target(admin_url):
    if os.getenv('FLY_APP_NAME') not in APPS:
        raise SystemExit('Refusing to run outside a PathSix backend Fly app.')
    runtime = make_url(os.getenv('DATABASE_URL', '').replace('postgres://', 'postgresql://', 1))
    admin = make_url(admin_url)
    if set(admin.query) - ALLOWED_QUERY:
        raise SystemExit('Refusing an administrator URL with connection overrides in its query string.')
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
                      'retention_days': RETENTION_DAYS, 'rows': counts}))


if __name__ == '__main__':
    main()
