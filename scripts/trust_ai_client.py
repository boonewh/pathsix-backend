"""Operator-only: mark a vetted AI host as a trusted client that may use the inline card.

The inline approval card relies on the host keeping private result metadata
from the model and honoring app-only tool visibility. Only hosts the user has
approved for that may be trusted here. The trusted row is operator-managed: it
is no longer refreshed from the host's CIMD document, so a changed redirect
must be updated here (scripts/check_ai_client_documents.py reports changes).

The administrator URL arrives on stdin. Default is a preview; --apply writes.
Usage: python scripts/trust_ai_client.py --client chatgpt [--apply]
"""
import argparse
import json
import os
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

# User decision, November 8, 2026: trust ChatGPT for the inline card.
TRUSTED = {
    'chatgpt': {'id': 'https://chatgpt.com/oauth/client.json', 'name': 'ChatGPT',
                'redirect_uris': ['https://chatgpt.com/connector_platform_oauth_redirect']},
}
# Keep equal to app.services.ai_connections.PUBLISHED_SCOPES (a test enforces it).
SCOPES = ['clients:read', 'contacts:create', 'contacts:read', 'interactions:create', 'interactions:read',
          'leads:create', 'leads:read', 'projects:create', 'projects:read']
TARGETS = {
    'pathsixsolutions-backend-staging': ('pathsixsolutions-db-staging', 'pathsixsolutions_backend_staging'),
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--client', choices=sorted(TRUSTED), required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    app = os.getenv('FLY_APP_NAME')
    if app not in TARGETS:
        raise RuntimeError('Refusing unexpected app')
    host, database = TARGETS[app]
    url = make_url(sys.stdin.readline().strip().replace('postgres://', 'postgresql://', 1))
    if url.host not in {host + '.flycast', host + '.internal'} or url.database != database:
        raise RuntimeError('Refusing unexpected operator connection')
    client = TRUSTED[args.client]
    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.begin() as c:
            c.execute(text("SET LOCAL lock_timeout='5s'"))
            before = c.execute(text('SELECT registration, inline_review, is_active, oauth_enabled, redirect_uris::text '
                                    'FROM public.ai_clients WHERE id=:id FOR UPDATE'), {'id': client['id']}).first()
            if args.apply:
                params = {'id': client['id'], 'name': client['name'],
                          'redirects': json.dumps(client['redirect_uris']), 'scopes': json.dumps(SCOPES)}
                if before is None:
                    c.execute(text('INSERT INTO public.ai_clients (id, name, is_active, allowed_scopes, redirect_uris, '
                                   'oauth_enabled, registration, inline_review) VALUES (:id, :name, true, '
                                   'CAST(:scopes AS json), CAST(:redirects AS json), true, \'operator\', true)'), params)
                else:
                    c.execute(text('UPDATE public.ai_clients SET name=:name, is_active=true, oauth_enabled=true, '
                                   'allowed_scopes=CAST(:scopes AS json), redirect_uris=CAST(:redirects AS json), '
                                   "registration='operator', inline_review=true WHERE id=:id"), params)
            after = c.execute(text('SELECT registration, inline_review, is_active, oauth_enabled, redirect_uris::text '
                                   'FROM public.ai_clients WHERE id=:id'), {'id': client['id']}).first()
            connections = c.execute(text('SELECT count(*) FROM public.ai_connections WHERE client_id=:id AND revoked_at IS NULL'),
                                    {'id': client['id']}).scalar_one()
            print(json.dumps({'applied': args.apply, 'client': client['id'],
                              'before': list(before) if before else None,
                              'after': list(after) if after else None, 'active_connections': connections}))
    finally:
        engine.dispose()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('Trust update failed: ' + type(exc).__name__, file=sys.stderr)
        raise SystemExit(1)
