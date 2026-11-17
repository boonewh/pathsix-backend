"""Reviewed creation of contacts, projects and interactions by AI clients."""
from alembic import op
from sqlalchemy import text

from migrations.versions.mcp_client_registration import create_register_function

revision = 'mcp_record_creation'
down_revision = 'mcp_published_scopes_v2'
branch_labels = depends_on = None

KINDS = ('create_lead', 'create_contact', 'create_project', 'create_interaction')
# Keep in step with app.services.ai_connections.PUBLISHED_SCOPES.
PUBLISHED = ('clients:read', 'contacts:create', 'contacts:read', 'interactions:create', 'interactions:read',
             'leads:create', 'leads:read', 'projects:create', 'projects:read')


def secure(connection, schema, runtime_role):
    if connection.dialect.name != 'postgresql' or not runtime_role:
        raise RuntimeError('PostgreSQL and explicit runtime role required')
    create_register_function(connection, schema, runtime_role, PUBLISHED, replace=True)


def reconcile(connection, schema, role):
    if connection.dialect.name != 'postgresql' or not role:
        raise RuntimeError('PostgreSQL and explicit runtime role required')
    connection.execute(text("SET LOCAL lock_timeout='5s'"))
    connection.execute(text("SET LOCAL statement_timeout='60s'"))
    q = connection.dialect.identifier_preparer.quote
    actions = f'{q(schema)}.{q("ai_write_actions")}'
    allowed = ','.join(f"'{kind}'" for kind in KINDS)
    connection.execute(text(f'ALTER TABLE {actions} DROP CONSTRAINT ck_ai_write_actions_kind'))
    connection.execute(text(f'ALTER TABLE {actions} ADD CONSTRAINT ck_ai_write_actions_kind CHECK (kind IN ({allowed}))'))
    secure(connection, schema, role)
    # Self-registered clients may use every published scope; operator rows change
    # only deliberately (scripts/trust_ai_client.py). Grants keep approved scopes.
    connection.execute(text(f"UPDATE {q(schema)}.{q('ai_clients')} SET allowed_scopes = CAST(:scopes AS json) "
                            f"WHERE registration IN ('cimd', 'dcr')"),
                       {'scopes': '[' + ','.join(f'"{s}"' for s in PUBLISHED) + ']'})


def upgrade():
    connection = op.get_bind()
    reconcile(connection, connection.execute(text('SELECT current_schema()')).scalar_one(),
              op.get_context().config.attributes.get('runtime_role'))


def downgrade():
    raise RuntimeError('Preserve proposals and receipts of every kind; use a reviewed migration')
