"""Operator-only preflight/migration; administrative URL arrives via stdin.

Targets (staging, rehearsal, production) are fixed in database_targets.py.
Dry run by default: a full rehearsal inside a transaction that rolls back.
"""
import argparse
import json
import os
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parent))
from database_targets import TARGETS, checked_admin_url, target  # noqa: E402

OLD_HEADS = {'add_project_assigned_to', 'add_subscriptions_table', 'add_tenants_table'}
NEW_HEAD = 'tenant_membership_indexes'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--revision', choices=[NEW_HEAD, 'tenant_relationships', 'tenant_rls_prepare', 'tenant_row_security', 'parent_link_rules', 'ai_consent_grants', 'oauth_browser_flow', 'mcp_read_audit', 'mcp_lead_creation', 'mcp_client_registration', 'ai_connection_idle_expiry', 'mcp_published_scopes_v2', 'mcp_record_creation', 'mcp_record_updates', 'contacts_unified'], default=NEW_HEAD)
    parser.add_argument('--target', choices=sorted(TARGETS), default='staging')
    parser.add_argument('--max-rows', type=int, default=10000)
    args = parser.parse_args()
    spec = target(args.target)
    url = checked_admin_url(args.target, sys.stdin.readline(), os.getenv('FLY_APP_NAME'))
    role = spec['runtime_role']
    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.begin() as connection:
            connection.execute(text('SET LOCAL search_path=public'))
            connection.execute(text("SET LOCAL lock_timeout='5s'"))
            connection.execute(text("SET LOCAL statement_timeout='60s'"))
            heads = set(connection.execute(text('SELECT version_num FROM public.alembic_version')).scalars())
            predecessors = {'tenant_relationships': NEW_HEAD, 'tenant_rls_prepare': 'tenant_relationships', 'tenant_row_security': 'tenant_rls_prepare', 'parent_link_rules': 'tenant_row_security', 'ai_consent_grants': 'parent_link_rules', 'oauth_browser_flow':'ai_consent_grants','mcp_read_audit':'oauth_browser_flow','mcp_lead_creation':'mcp_read_audit','mcp_client_registration':'mcp_lead_creation','ai_connection_idle_expiry':'mcp_client_registration','mcp_published_scopes_v2':'ai_connection_idle_expiry','mcp_record_creation':'mcp_published_scopes_v2','mcp_record_updates':'mcp_record_creation','contacts_unified':'mcp_record_updates'}
            allowed_heads = (OLD_HEADS, {NEW_HEAD}) if args.revision == NEW_HEAD else ({predecessors[args.revision]}, {args.revision})
            if heads not in allowed_heads:
                raise RuntimeError('Unexpected migration history; refusing to replay legacy migrations')
            root = Path(__file__).resolve().parents[1]
            sys.path.insert(0, str(root))
            from migrations.versions.tenant_membership_indexes import TABLES, reconcile
            if args.revision == 'tenant_relationships':
                from migrations.versions.tenant_relationships import reconcile
            elif args.revision == 'tenant_rls_prepare':
                from migrations.versions.tenant_rls_prepare import reconcile as prepare
                reconcile = lambda c, s: prepare(c, s, role)
            elif args.revision == 'tenant_row_security':
                if os.getenv('CRM_RLS_ENABLED') != '1':
                    raise RuntimeError('Enable application database identity before activating RLS')
                from migrations.versions.tenant_row_security import reconcile
            elif args.revision == 'parent_link_rules':
                from migrations.versions.parent_link_rules import reconcile
            elif args.revision == 'ai_consent_grants':
                if os.getenv('CRM_RLS_ENABLED') != '1':
                    raise RuntimeError('Enable application database identity before consent storage')
                from migrations.versions.ai_consent_grants import reconcile as consent
                reconcile = lambda c, s: consent(c, s, role)
            elif args.revision == 'oauth_browser_flow':
                if os.getenv('CRM_RLS_ENABLED') != '1':
                    raise RuntimeError('Enable application database identity before OAuth storage')
                from migrations.versions.oauth_browser_flow import reconcile as oauth
                reconcile = lambda c, s: oauth(c, s, role)
            elif args.revision == 'mcp_read_audit':
                if os.getenv('CRM_RLS_ENABLED') != '1':
                    raise RuntimeError('Enable application database identity before MCP storage')
                from migrations.versions.mcp_read_audit import reconcile as mcp_audit
                reconcile = lambda c, s: mcp_audit(c, s, role)
            elif args.revision == 'mcp_lead_creation':
                if os.getenv('CRM_RLS_ENABLED') != '1':
                    raise RuntimeError('Enable application database identity before write proposals')
                from migrations.versions.mcp_lead_creation import reconcile as actions
                reconcile = lambda c, s: actions(c, s, role)
            elif args.revision == 'mcp_client_registration':
                if os.getenv('CRM_RLS_ENABLED') != '1':
                    raise RuntimeError('Enable application database identity before client registration')
                from migrations.versions.mcp_client_registration import reconcile as clients
                reconcile = lambda c, s: clients(c, s, role)
            elif args.revision == 'ai_connection_idle_expiry':
                from migrations.versions.ai_connection_idle_expiry import reconcile
            elif args.revision == 'mcp_published_scopes_v2':
                from migrations.versions.mcp_published_scopes_v2 import reconcile as published
                reconcile = lambda c, s: published(c, s, role)
            elif args.revision == 'mcp_record_creation':
                from migrations.versions.mcp_record_creation import reconcile as creation
                reconcile = lambda c, s: creation(c, s, role)
            elif args.revision == 'mcp_record_updates':
                from migrations.versions.mcp_record_updates import reconcile as updates
                reconcile = lambda c, s: updates(c, s, role)
            elif args.revision == 'contacts_unified':
                from migrations.versions.contacts_unified import reconcile
            counts = {table: connection.execute(text(f'SELECT count(*) FROM public.{table}')).scalar_one()
                      for table in TABLES}
            # This transactional plan is deliberately limited to small databases.
            if max(counts.values()) > args.max_rows:
                raise RuntimeError('Database exceeds bounded migration size; review online DDL plan')
            if args.apply:
                cfg = Config(str(root / 'alembic.ini'))
                cfg.set_main_option('script_location', str(root / 'migrations'))
                cfg.attributes['connection'] = connection
                cfg.attributes['version_table_schema'] = 'public'
                cfg.attributes['runtime_role'] = role
                command.upgrade(cfg, args.revision)
            else:
                # Full rehearsal, including constraints/indexes, always rolls back.
                with connection.begin_nested() as rehearsal:
                    result = reconcile(connection, 'public')
                    rehearsal.rollback()
                if isinstance(result, dict):
                    counts['rehearsal'] = result
            final_heads = list(connection.execute(text('SELECT version_num FROM public.alembic_version')).scalars())
            print(json.dumps({'applied': args.apply, 'heads': sorted(final_heads), 'row_counts': counts}))
    finally:
        engine.dispose()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Drivers can include connection strings in tracebacks. Print no values.
        print('Migration failed: ' + type(exc).__name__, file=sys.stderr)
        raise SystemExit(1)
