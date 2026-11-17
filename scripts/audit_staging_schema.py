"""Read-only schema/relationship inventory; refuses non-staging Fly hosts."""
import json
import os
from urllib.parse import urlparse
from sqlalchemy import create_engine, inspect, text, MetaData, select, func, or_


def main():
    url = os.environ['DATABASE_URL'].replace('postgres://', 'postgresql://', 1)
    if (os.getenv('FLY_APP_NAME') != 'pathsixsolutions-backend-staging' or
        urlparse(url).hostname not in {'pathsixsolutions-db-staging.flycast', 'pathsixsolutions-db-staging.internal'}):
        raise SystemExit('Refusing non-staging environment')
    engine = create_engine(url)
    with engine.connect() as connection, connection.begin():
        connection.execute(text('SET TRANSACTION READ ONLY'))
        connection.execute(text("SET LOCAL statement_timeout = '15s'"))
        inspector = inspect(connection)
        metadata = MetaData()
        metadata.reflect(connection, schema='public')
        tables = {table.name: table for table in metadata.tables.values()}
        report = {'tables': [], 'invalid_relationships': []}
        for name, table in sorted(tables.items()):
            if name == 'alembic_version':
                continue
            fks = inspector.get_foreign_keys(name, schema='public')
            report['tables'].append({
                'table': name, 'tenant_column': 'tenant_id' in table.c,
                'tenant_foreign_key': any(fk['constrained_columns'] == ['tenant_id'] for fk in fks),
                'composite_foreign_keys': [fk['name'] for fk in fks if len(fk['constrained_columns']) > 1],
                'tenant_indexes': [idx['name'] for idx in inspector.get_indexes(name, schema='public') if 'tenant_id' in idx['column_names']],
            })
            # Validate tenant membership even when no FK has been declared.
            relationships = []
            if 'tenant_id' in table.c:
                relationships.append(('tenant_id', 'tenants', 'id'))
            for fk in fks:
                if len(fk['constrained_columns']) == 1 and fk['constrained_columns'] != ['tenant_id']:
                    relationships.append((fk['constrained_columns'][0], fk['referred_table'], fk['referred_columns'][0]))
            for column, target, target_column in relationships:
                if target not in tables:
                    continue
                parent = tables[target].alias('parent')
                invalid = parent.c[target_column].is_(None)
                if column != 'tenant_id' and 'tenant_id' in table.c and 'tenant_id' in parent.c:
                    invalid = or_(invalid, table.c.tenant_id != parent.c.tenant_id)
                count = connection.execute(select(func.count()).select_from(
                    table.outerjoin(parent, table.c[column] == parent.c[target_column])
                ).where(table.c[column].is_not(None), invalid)).scalar_one()
                report['invalid_relationships'].append({'relationship': f'{name}.{column}->{target}', 'count': count})
        report['rls'] = [dict(row) for row in connection.execute(text(
            "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
            "JOIN pg_namespace n ON n.oid = relnamespace WHERE n.nspname='public' AND relkind='r'"
        )).mappings()]
        report['database_role'] = dict(connection.execute(text(
            'SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user'
        )).mappings().one())
        print(json.dumps(report, sort_keys=True))
    engine.dispose()


if __name__ == '__main__':
    main()
