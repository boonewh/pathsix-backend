"""Check the running app shows every user exactly their records. Prints counts only.

Runs the real app with this machine's DATABASE_URL (the restricted runtime login
with row security) and compares each list endpoint with the database's own
counts, read through the administrator login on stdin. No names or emails print.

    <admin-url> | python scripts/verify_tenant_access.py --target production
"""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent))
from database_targets import TARGETS, checked_admin_url, target  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

USERS = '''SELECT u.id, u.tenant_id, u.email, u.is_active, COALESCE(bool_or(r.name = 'admin'), false)
           FROM users u LEFT JOIN user_roles ur ON ur.user_id = u.id LEFT JOIN roles r ON r.id = ur.role_id
           GROUP BY 1, 2, 3, 4 ORDER BY 1'''
OWNED = '''SELECT count(*) FROM {table} WHERE tenant_id = :t AND deleted_at IS NULL
           AND (assigned_to = :u OR (assigned_to IS NULL AND created_by = :u))'''
COMPANY = 'SELECT count(*) FROM {table} WHERE tenant_id = :t AND deleted_at IS NULL'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--target', choices=sorted(TARGETS), required=True)
    args = parser.parse_args()
    spec = target(args.target)
    admin = checked_admin_url(args.target, sys.stdin.readline(), os.getenv('FLY_APP_NAME'))
    if os.getenv('CRM_RLS_ENABLED') != '1':
        raise SystemExit('Run this where the app uses the restricted login (CRM_RLS_ENABLED=1).')
    from app import create_app
    from app.database import SessionLocal, engine
    from app.models import User
    from app.services.database_context import auth_lookup
    from app.utils.auth_utils import create_token
    if engine.url.username != spec['runtime_role']:
        raise SystemExit('DATABASE_URL is not the restricted runtime login.')
    truth = create_engine(admin, hide_parameters=True)
    with truth.connect() as c:
        c.execute(text('SET TRANSACTION READ ONLY'))
        users = c.execute(text(USERS)).all()
        expected = {}
        for uid, tid, _, _, is_admin in users:
            row = {t: c.execute(text(OWNED.format(table=t)), {'t': tid, 'u': uid}).scalar() for t in ('clients', 'leads')}
            if is_admin:
                row.update({t + '/all': c.execute(text(COMPANY.format(table=t)), {'t': tid}).scalar()
                            for t in ('clients', 'leads')})
            expected[uid] = row
    truth.dispose()
    app = create_app()
    results, problems = [], []

    async def check():
        client = app.test_client()
        for uid, tid, email, active, is_admin in users:
            row = {'user': uid, 'company': tid, 'admin': is_admin, 'active': active}
            with SessionLocal() as s:
                auth_lookup(s, email=email.strip().lower())
                found = s.query(User).filter_by(email=email.strip().lower()).one_or_none()
                row['login_lookup'] = found is not None and found.id == uid
            if active and not row['login_lookup']:
                problems.append((uid, 'login lookup failed'))
            if not active:
                results.append(row)
                continue
            with SessionLocal() as s:
                auth_lookup(s, user_id=uid)
                user = s.get(User, uid)
                async with app.app_context():
                    token = create_token(user)
            headers = {'Authorization': 'Bearer ' + token}
            paths = ['clients', 'leads', 'projects', 'interactions'] + (['clients/all', 'leads/all'] if is_admin else [])
            for path in paths:
                url = f'/api/{path}?per_page=200&page=1' if '/' in path else f'/api/{path}/?per_page=200&page=1'
                response = await client.get(url, headers=headers)
                body = await response.get_json() if response.status_code == 200 else None
                total = None
                if isinstance(body, dict):
                    total = body.get('total', body.get('total_count'))
                    if total is None:
                        total = next((len(v) for v in body.values() if isinstance(v, list)), None)
                elif isinstance(body, list):
                    total = len(body)
                row[path] = total if response.status_code == 200 else response.status_code
                if response.status_code != 200:
                    problems.append((uid, path, response.status_code))
                elif path in expected[uid] and total != expected[uid][path]:
                    problems.append((uid, path, f'saw {total}, database has {expected[uid][path]}'))
            results.append(row)

    asyncio.run(check())
    for row in results:
        print(json.dumps(row))
    print(json.dumps({'problems': problems}))
    raise SystemExit(1 if problems else 0)


if __name__ == '__main__':
    main()
