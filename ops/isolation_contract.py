"""Read-only isolation attestation; rejects drift without changing any policy.

Policy expressions are the reviewed PostgreSQL deparse of tenant_rls_prepare.
Whitespace may vary; semantic/parenthesis/cast changes require explicit review.
Call within the intended schema's search_path using a transaction-scoped connection.
"""
import re
from sqlalchemy import text
from migrations.versions.tenant_rls_prepare import TABLES as LEGACY_TABLES, BUSINESS

TABLES = LEGACY_TABLES + ('ai_connections',)


def expected_policies():
    setting = lambda key: f"(NULLIF(current_setting('crm.{key}'::text, true), ''::text))::integer"
    tenant, auth_user, auth_tenant, reset = [setting(k) for k in
        ('tenant_id','auth_user_id','auth_tenant_id','reset_user_id')]
    own = f'(tenant_id = {tenant})'
    result = {(name,'crm_all','ALL'):(own,own) for name in BUSINESS}
    connection_owner = f'({own} AND (user_id = {setting("user_id")}))'
    result[('ai_connections','crm_all','ALL')] = (connection_owner,connection_owner)
    result[('tenants','crm_select','SELECT')] = (f'((id = {tenant}) OR (id = {auth_tenant}))',None)
    result[('users','crm_select','SELECT')] = (f'({own} OR ((id = {auth_user}) AND (tenant_id = {auth_tenant})))',None)
    result[('users','crm_insert','INSERT')] = (None,own)
    result[('users','crm_delete','DELETE')] = (own,None)
    result[('users','crm_update','UPDATE')] = (f'({own} OR (id = {reset}))',
        f'({own} OR ((id = {reset}) AND (tenant_id = {auth_tenant})))')
    for name in ('user_preferences','user_roles'):
        owner = f'(EXISTS ( SELECT 1 FROM users u WHERE ((u.id = {name}.user_id) AND (u.tenant_id = {tenant}))))'
        if name == 'user_preferences':
            result[(name,'crm_all','ALL')] = (owner,owner)
        else:
            result[(name,'crm_select','SELECT')] = (f'({owner} OR (user_id = {auth_user}))',None)
            result[(name,'crm_insert','INSERT')] = (None,owner)
            result[(name,'crm_update','UPDATE')] = (owner,owner)
            result[(name,'crm_delete','DELETE')] = (owner,None)
    return result


def verify_isolation_contract(connection, schema, runtime_role):
    if connection.dialect.name != 'postgresql':
        raise RuntimeError('PostgreSQL isolation verification required')
    if connection.execute(text('SELECT current_schema()')).scalar_one() != schema:
        raise RuntimeError('Isolation verification schema mismatch')
    role = connection.execute(text(
        'SELECT rolsuper,rolbypassrls,rolcreaterole,rolcreatedb FROM pg_roles WHERE rolname=:role'
    ), {'role':runtime_role}).first()
    if role is None or any(role):
        raise RuntimeError('Runtime role has unsafe database privileges')
    elevated_membership = connection.execute(text(
        'SELECT EXISTS (SELECT 1 FROM pg_roles WHERE (rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb) '
        'AND pg_has_role(:role,oid,\'MEMBER\'))'
    ), {'role':runtime_role}).scalar_one()
    if elevated_membership:
        raise RuntimeError('Runtime role can assume elevated database privileges')
    tables = connection.execute(text(
        "SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity,pg_get_userbyid(c.relowner) AS owner "
        "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname=:schema AND c.relkind='r'"
    ), {'schema':schema}).all()
    state = {row.relname:row for row in tables}
    for table in TABLES:
        row = state.get(table)
        if row is None or not row.relrowsecurity or not row.relforcerowsecurity or row.owner == runtime_role:
            raise RuntimeError(f'Row security/ownership drift: {table}')
    rows = connection.execute(text(
        'SELECT tablename,policyname,cmd,permissive,roles,qual,with_check FROM pg_policies WHERE schemaname=:schema'
    ), {'schema':schema}).mappings().all()
    actual = {(r['tablename'],r['policyname'],r['cmd']):r for r in rows if r['tablename'] in TABLES}
    expected = expected_policies()
    if actual.keys() != expected.keys():
        raise RuntimeError('Isolation policy inventory drift')
    compact = lambda value: None if value is None else re.sub(r'\s+', '', value)
    for key, expressions in expected.items():
        row = actual[key]
        if (row['permissive'] != 'PERMISSIVE' or row['roles'] != ['public']
                or tuple(map(compact,(row['qual'],row['with_check']))) != tuple(map(compact,expressions))):
            raise RuntimeError(f'Isolation policy expression drift: {key[0]}/{key[1]}')
    return {'tables':len(TABLES),'policies':len(expected),'role_restricted':True}
