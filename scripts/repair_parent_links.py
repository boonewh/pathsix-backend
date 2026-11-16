"""Repair records that break the parent-link rules before `parent_link_rules` runs.

Dry run unless --apply. The admin login arrives on stdin and must match the
target in database_targets.py. Nothing happens unless the records found are
exactly the ones the owner reviewed (--expect-projects / --expect-interactions),
so data that changed since review stops the repair instead of being guessed at.

- A project linked to both a client and a lead keeps the client and drops the
  lead link (the client is the current owner after a conversion).
- An interaction attached to no client, lead or project is deleted; it cannot be
  opened anywhere in the CRM.
- Contacts that break the rule are never repaired here; they stop the run.

    printenv DATABASE_URL | python scripts/repair_parent_links.py --target production \\
        --expect-projects 5,6,330 --expect-interactions 829,948,1092 [--apply]
"""
import argparse
import json
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent))
from database_targets import TARGETS, checked_admin_url  # noqa: E402

BOTH_PARENTS = 'SELECT id FROM projects WHERE client_id IS NOT NULL AND lead_id IS NOT NULL ORDER BY id'
NO_PARENT = 'SELECT id FROM interactions WHERE num_nonnulls(client_id, lead_id, project_id) = 0 ORDER BY id'
BAD_INTERACTIONS = 'SELECT count(*) FROM interactions WHERE num_nonnulls(client_id, lead_id, project_id) > 1'
BAD_CONTACTS = 'SELECT count(*) FROM contacts WHERE num_nonnulls(client_id, lead_id) <> 1'


def ids(value):
    return sorted({int(part) for part in value.split(',') if part.strip()}) if value else []


def repair(connection, expect_projects, expect_interactions, apply):
    projects = list(connection.execute(text(BOTH_PARENTS)).scalars())
    interactions = list(connection.execute(text(NO_PARENT)).scalars())
    other = {'interactions with several parents': connection.execute(text(BAD_INTERACTIONS)).scalar(),
             'contacts without exactly one parent': connection.execute(text(BAD_CONTACTS)).scalar()}
    if any(other.values()):
        raise SystemExit(f'Unreviewed rule-breaking records: {other}; nothing changed.')
    if projects != expect_projects or interactions != expect_interactions:
        raise SystemExit(f'Found projects {projects} and interactions {interactions}, which differ from '
                         f'the reviewed {expect_projects} and {expect_interactions}; nothing changed.')
    changed = {'projects lead link cleared': 0, 'interactions deleted': 0}
    if apply:
        if projects:
            changed['projects lead link cleared'] = connection.execute(text(
                'UPDATE projects SET lead_id = NULL WHERE id = ANY(:ids) AND client_id IS NOT NULL '
                'AND lead_id IS NOT NULL'), {'ids': projects}).rowcount
        if interactions:
            changed['interactions deleted'] = connection.execute(text(
                'DELETE FROM interactions WHERE id = ANY(:ids) '
                'AND num_nonnulls(client_id, lead_id, project_id) = 0'), {'ids': interactions}).rowcount
        if changed != {'projects lead link cleared': len(projects), 'interactions deleted': len(interactions)}:
            raise RuntimeError('Repair touched an unexpected number of rows; rolled back.')
    return {'projects': projects, 'interactions': interactions, 'changed': changed}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--target', choices=sorted(TARGETS), required=True)
    parser.add_argument('--expect-projects', type=ids, default=[])
    parser.add_argument('--expect-interactions', type=ids, default=[])
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    url = checked_admin_url(args.target, sys.stdin.readline(), os.getenv('FLY_APP_NAME'))
    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.begin() as connection:
            connection.execute(text('SET LOCAL search_path=public'))
            connection.execute(text("SET LOCAL lock_timeout='5s'"))
            result = repair(connection, args.expect_projects, args.expect_interactions, args.apply)
        print(json.dumps({'target': args.target, 'applied': args.apply, **result}))
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
