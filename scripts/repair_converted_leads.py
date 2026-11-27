"""Move records stranded on converted leads to the client each lead became.

Before the conversion fix, the web converted a lead with separate calls and
nothing moved its projects or contacts (and a failed transfer step left its
interactions). A project or contact on a deleted lead is hidden from everyone.

A record is stranded when its lead is deleted and exactly one live client of the
same tenant was made from that lead (clients.source_lead_id). It moves to that
client. Leads deleted without becoming a client are ordinary deletions and are
left alone. Archived projects move too, so they reappear correctly if restored.

Dry run unless --apply. The admin login arrives on stdin and must match the
target in database_targets.py. Nothing changes unless the records found are
exactly the ones the owner reviewed, so data that changed since review stops
the repair. Output holds record ids and counts only.

    <admin-url helper> | flyctl ssh console --app <app> -C \\
        "python scripts/repair_converted_leads.py --target production \\
         --expect-projects 1,2 --expect-contacts 3 --expect-interactions 4 [--apply]"
"""
import argparse
import json
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent))
from database_targets import TARGETS, checked_admin_url  # noqa: E402

# Converted leads: deleted, with live clients made from them in the same tenant.
CONVERTED = '''
    SELECT l.id AS lead_id, l.tenant_id, array_agg(c.id ORDER BY c.id) AS clients
    FROM leads l JOIN clients c ON c.source_lead_id = l.id AND c.tenant_id = l.tenant_id
                                AND c.deleted_at IS NULL
    WHERE l.deleted_at IS NOT NULL
    GROUP BY l.id, l.tenant_id
'''
STRANDED = {
    'projects': 'SELECT p.id, p.lead_id FROM projects p WHERE p.lead_id = ANY(:leads) AND p.client_id IS NULL',
    'contacts': 'SELECT k.id, k.lead_id FROM contacts k WHERE k.lead_id = ANY(:leads) AND k.client_id IS NULL',
    'interactions': ('SELECT i.id, i.lead_id FROM interactions i WHERE i.lead_id = ANY(:leads) '
                     'AND i.client_id IS NULL AND i.project_id IS NULL'),
}
MOVE = {
    'projects': ('UPDATE projects SET lead_id = NULL, client_id = :client WHERE id = :id AND lead_id = :lead '
                 'AND client_id IS NULL AND tenant_id = :tenant'),
    'contacts': ('UPDATE contacts SET lead_id = NULL, client_id = :client WHERE id = :id AND lead_id = :lead '
                 'AND client_id IS NULL AND tenant_id = :tenant'),
    'interactions': ('UPDATE interactions SET lead_id = NULL, client_id = :client WHERE id = :id '
                     'AND lead_id = :lead AND client_id IS NULL AND project_id IS NULL AND tenant_id = :tenant'),
}


def ids(value):
    return sorted({int(part) for part in value.split(',') if part.strip()}) if value else []


def repair(connection, expected, apply):
    """expected: {'projects': [...], 'contacts': [...], 'interactions': [...]}."""
    converted = connection.execute(text(CONVERTED)).mappings().all()
    ambiguous = sorted(row['lead_id'] for row in converted if len(row['clients']) > 1)
    target = {row['lead_id']: (row['clients'][0], row['tenant_id'])
              for row in converted if len(row['clients']) == 1}
    found = {kind: connection.execute(text(sql), {'leads': list(target)}).all() for kind, sql in STRANDED.items()}
    report = {kind: sorted(row.id for row in rows) for kind, rows in found.items()}
    stuck = {kind: connection.execute(text(sql), {'leads': ambiguous}).all() for kind, sql in STRANDED.items()}
    if any(stuck.values()):
        raise SystemExit(f'Leads {ambiguous} have several live clients and stranded records; nothing changed.')
    if report != {kind: sorted(expected.get(kind, [])) for kind in STRANDED}:
        raise SystemExit(f'Found {report}, which differs from the reviewed {expected}; nothing changed.')
    changed = {kind: 0 for kind in STRANDED}
    if apply:
        for kind, rows in found.items():
            for row in rows:
                client, tenant = target[row.lead_id]
                changed[kind] += connection.execute(text(MOVE[kind]), {
                    'id': row.id, 'lead': row.lead_id, 'client': client, 'tenant': tenant}).rowcount
        if changed != {kind: len(rows) for kind, rows in found.items()}:
            raise RuntimeError('Repair touched an unexpected number of rows; rolled back.')
    return {'found': report, 'leads': len({row.lead_id for rows in found.values() for row in rows}),
            'changed': changed}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--target', choices=sorted(TARGETS), required=True)
    for kind in STRANDED:
        parser.add_argument(f'--expect-{kind}', type=ids, default=[])
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    expected = {kind: getattr(args, f'expect_{kind}') for kind in STRANDED}
    url = checked_admin_url(args.target, sys.stdin.readline(), os.getenv('FLY_APP_NAME'))
    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.begin() as connection:
            connection.execute(text('SET LOCAL search_path=public'))
            connection.execute(text("SET LOCAL lock_timeout='5s'"))
            result = repair(connection, expected, args.apply)
        print(json.dumps({'target': args.target, 'applied': args.apply, **result}))
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
