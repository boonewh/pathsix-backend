"""Publish contact, project and interaction read scopes for AI clients."""
from alembic import op
from sqlalchemy import text

from migrations.versions.mcp_client_registration import create_register_function

revision = 'mcp_published_scopes_v2'
down_revision = 'ai_connection_idle_expiry'
branch_labels = depends_on = None

# Keep in step with app.services.ai_connections.PUBLISHED_SCOPES.
PUBLISHED = ('clients:read', 'contacts:read', 'interactions:read', 'leads:create', 'leads:read', 'projects:read')


def secure(connection, schema, runtime_role):
    if connection.dialect.name != 'postgresql' or not runtime_role:
        raise RuntimeError('PostgreSQL and explicit runtime role required')
    create_register_function(connection, schema, runtime_role, PUBLISHED, replace=True)


def reconcile(connection, schema, role):
    if connection.dialect.name != 'postgresql' or not role:
        raise RuntimeError('PostgreSQL and explicit runtime role required')
    connection.execute(text("SET LOCAL lock_timeout='5s'"))
    connection.execute(text("SET LOCAL statement_timeout='60s'"))
    secure(connection, schema, role)
    # Self-registered clients may use every published scope; operator rows are
    # changed only deliberately (scripts/trust_ai_client.py). Existing grants
    # keep their approved scopes until the user approves the new ones.
    q = connection.dialect.identifier_preparer.quote
    connection.execute(text(f"UPDATE {q(schema)}.{q('ai_clients')} SET allowed_scopes = CAST(:scopes AS json) "
                            f"WHERE registration IN ('cimd', 'dcr')"), {'scopes': '[' + ','.join(f'"{s}"' for s in PUBLISHED) + ']'})


def upgrade():
    connection = op.get_bind()
    reconcile(connection, connection.execute(text('SELECT current_schema()')).scalar_one(),
              op.get_context().config.attributes.get('runtime_role'))


def downgrade():
    raise RuntimeError('Narrowing published scopes needs a reviewed migration')
