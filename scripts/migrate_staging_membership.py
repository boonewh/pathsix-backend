"""Operator-only staging preflight/migration; administrative URL arrives via stdin."""
import argparse
import json
import os
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

OLD_HEADS = {'add_project_assigned_to', 'add_subscriptions_table', 'add_tenants_table'}
NEW_HEAD = 'tenant_membership_indexes'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--revision', choices=[NEW_HEAD, 'tenant_relationships', 'tenant_rls_prepare', 'tenant_row_security', 'parent_link_rules'], default=NEW_HEAD)
    args = parser.parse_args()
    if os.getenv('FLY_APP_NAME') != 'pathsixsolutions-backend-staging':
        raise RuntimeError('Refusing non-staging app')
    url = make_url(sys.stdin.readline().strip().replace('postgres://', 'postgresql://', 1))
    if (url.host not in {'pathsixsolutions-db-staging.flycast', 'pathsixsolutions-db-staging.internal'}
            or url.database != 'pathsixsolutions_backend_staging'
            or url.username != 'pathsixsolutions_backend_staging'):
        raise RuntimeError('Refusing unexpected operator connection')
    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.begin() as connection:
            connection.execute(text('SET LOCAL search_path=public'))
            connection.execute(text("SET LOCAL lock_timeout='5s'"))
            connection.execute(text("SET LOCAL statement_timeout='60s'"))
            heads = set(connection.execute(text('SELECT version_num FROM public.alembic_version')).scalars())
            predecessors = {'tenant_relationships': NEW_HEAD, 'tenant_rls_prepare': 'tenant_relationships', 'tenant_row_security': 'tenant_rls_prepare', 'parent_link_rules': 'tenant_row_security'}
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
                reconcile = lambda c, s: prepare(c, s, 'pathsix_crm_staging_runtime')
            elif args.revision == 'tenant_row_security':
                if os.getenv('CRM_RLS_ENABLED') != '1':
                    raise RuntimeError('Enable application database identity before activating RLS')
                from migrations.versions.tenant_row_security import reconcile
            elif args.revision == 'parent_link_rules':
                from migrations.versions.parent_link_rules import reconcile
            counts = {table: connection.execute(text(f'SELECT count(*) FROM public.{table}')).scalar_one()
                      for table in TABLES}
            # This transactional plan is deliberately limited to small staging data.
            if max(counts.values()) > 10000:
                raise RuntimeError('Staging exceeds bounded migration size; review online DDL plan')
            if args.apply:
                cfg = Config(str(root / 'alembic.ini'))
                cfg.set_main_option('script_location', str(root / 'migrations'))
                cfg.attributes['connection'] = connection
                cfg.attributes['version_table_schema'] = 'public'
                cfg.attributes['runtime_role'] = 'pathsix_crm_staging_runtime'
                command.upgrade(cfg, args.revision)
            else:
                # Full rehearsal, including constraints/indexes, always rolls back.
                with connection.begin_nested() as rehearsal:
                    reconcile(connection, 'public')
                    rehearsal.rollback()
            final_heads = list(connection.execute(text('SELECT version_num FROM public.alembic_version')).scalars())
            print(json.dumps({'applied': args.apply, 'heads': sorted(final_heads), 'row_counts': counts}))
    finally:
        engine.dispose()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Drivers can include connection strings in tracebacks. Print no values.
        print('Staging migration failed: ' + type(exc).__name__, file=sys.stderr)
        raise SystemExit(1)
