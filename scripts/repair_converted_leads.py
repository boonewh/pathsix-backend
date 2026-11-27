"""Move records stranded on converted leads to the client each lead became.

Before the conversion fix, the web converted a lead with separate calls and
nothing moved its projects or contacts (and a failed transfer step left its
interactions). A project or contact on a deleted lead is hidden from everyone.

A record is stranded when its lead is deleted and exactly one live client of the
same tenant was made from that lead (clients.source_lead_id). It moves to that
client. Leads deleted without becoming a client are ordinary deletions and are
left alone. Archived projects move too, so they reappear correctly if restored.
Child, lead and client must all belong to the same tenant.

Dry run unless --apply. The admin login arrives on stdin and must match the
target in database_targets.py. The reviewed plan names each record and its
destination client (record:client). Nothing changes unless the plan found is
exactly the reviewed one, and each write rechecks that its client is still the
lead's only live conversion, so data that changed since review stops the
repair. Output holds record ids and counts only.

    <admin-url helper> | flyctl ssh console --app <app> -C \\
        "python scripts/repair_converted_leads.py --target production \\
         --expect-projects 1:9,2:9 --expect-contacts 3:9 --expect-interactions 4:9 [--apply]"
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
    'projects': ('SELECT x.id, x.lead_id, x.tenant_id FROM projects x '
                 'WHERE x.lead_id = ANY(:leads) AND x.client_id IS NULL'),
    'contacts': ('SELECT x.id, x.lead_id, x.tenant_id FROM contacts x '
                 'WHERE x.lead_id = ANY(:leads) AND x.client_id IS NULL'),
    'interactions': ('SELECT x.id, x.lead_id, x.tenant_id FROM interactions x WHERE x.lead_id = ANY(:leads) '
                     'AND x.client_id IS NULL AND x.project_id IS NULL'),
}
# Each write rechecks that the client is still the lead's only live conversion.
GUARD = ('AND tenant_id = :tenant AND (SELECT array_agg(c.id) FROM clients c JOIN leads l ON l.id = c.source_lead_id AND l.tenant_id = c.tenant_id AND l.deleted_at IS NOT NULL WHERE c.source_lead_id = :lead AND c.tenant_id = :tenant AND c.deleted_at IS NULL) = ARRAY[:client]')
MOVE = {
    'projects': ('UPDATE projects SET lead_id = NULL, client_id = :client WHERE id = :id AND lead_id = :lead '
                 'AND client_id IS NULL ' + GUARD),
    'contacts': ('UPDATE contacts SET lead_id = NULL, client_id = :client WHERE id = :id AND lead_id = :lead '
                 'AND client_id IS NULL ' + GUARD),
    'interactions': ('UPDATE interactions SET lead_id = NULL, client_id = :client WHERE id = :id '
                     'AND lead_id = :lead AND client_id IS NULL AND project_id IS NULL ' + GUARD),
}


def plan(value):
    """'601:501,602:501' -> {601: 501, 602: 501} (record id: destination client id)."""
    pairs = [part.split(':') for part in value.split(',') if part.strip()] if value else []
    if any(len(pair) != 2 for pair in pairs):
        raise argparse.ArgumentTypeError('Use record:client pairs, like 601:501')
    return {int(record): int(client) for record, client in pairs}


def lock_reviewed(connection, expected):
    """Lock the reviewed destination clients, their source leads and every client
    made from those leads, by id, before anything is checked. Until commit no
    such lead or client can be deleted, restored or converted again (conversion
    locks its lead), so the checks below hold for the writes. Lead before client,
    as conversion does. source_lead_id itself is never changed by the app."""
    clients = sorted({client for pairs in expected.values() for client in pairs.values()})
    if not clients:
        return
    leads = sorted(connection.execute(text(
        'SELECT DISTINCT source_lead_id FROM clients WHERE id = ANY(:clients) AND source_lead_id IS NOT NULL'),
        {'clients': clients}).scalars())
    connection.execute(text('SELECT id FROM leads WHERE id = ANY(:leads) ORDER BY id FOR UPDATE'), {'leads': leads})
    connection.execute(text('SELECT id FROM clients WHERE id = ANY(:clients) OR source_lead_id = ANY(:leads) '
                            'ORDER BY id FOR UPDATE'), {'clients': clients, 'leads': leads})


def repair(connection, expected, apply):
    """expected: {'projects': {id: client}, 'contacts': {...}, 'interactions': {...}}.

    The plan found must equal the reviewed one, so every lead and client it uses
    was locked by lock_reviewed before discovery ran."""
    lock_reviewed(connection, expected)
    converted = connection.execute(text(CONVERTED)).mappings().all()
    ambiguous = sorted(row['lead_id'] for row in converted if len(row['clients']) > 1)
    target = {row['lead_id']: (row['clients'][0], row['tenant_id'])
              for row in converted if len(row['clients']) == 1}
    found = {kind: connection.execute(text(sql), {'leads': list(target)}).all() for kind, sql in STRANDED.items()}
    mismatched = sorted({row.lead_id for rows in found.values() for row in rows
                         if row.tenant_id != target[row.lead_id][1]})
    if mismatched:
        raise SystemExit(f'Records on leads {mismatched} belong to another tenant; nothing changed.')
    report = {kind: {row.id: target[row.lead_id][0] for row in rows} for kind, rows in found.items()}
    stuck = {kind: connection.execute(text(sql), {'leads': ambiguous}).all() for kind, sql in STRANDED.items()}
    if any(stuck.values()):
        raise SystemExit(f'Leads {ambiguous} have several live clients and stranded records; nothing changed.')
    if report != {kind: dict(expected.get(kind, {})) for kind in STRANDED}:
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
        parser.add_argument(f'--expect-{kind}', type=plan, default={})
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
        found = {kind: ','.join(f'{record}:{client}' for record, client in sorted(pairs.items()))
                 for kind, pairs in result['found'].items()}
        print(json.dumps({'target': args.target, 'applied': args.apply, **result, 'found': found}))
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
