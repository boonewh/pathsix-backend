"""Add tenants table for multi-tenant configuration

Revision ID: add_tenants_table
Revises: add_backup_tables
Create Date: 2025-01-25

This migration:
1. Creates the tenants table
2. Inserts tenant #1 (ASFI) with their config - MUST happen before FK constraint
3. Adds FK constraint from users.tenant_id -> tenants.id
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
import json


# revision identifiers, used by Alembic.
revision = 'add_tenants_table'
down_revision = 'add_backup_tables'
branch_labels = None
depends_on = None


# ASFI tenant configuration (tenant #1)
# This matches the frontend CRMConfig interface
ASFI_CONFIG = {
    "branding": {
        "companyName": "ASFI CRM",
        "primaryColor": "#2563eb",
        "secondaryColor": "#64748b",
    },
    "labels": {
        "client": "Client",
        "lead": "Lead",
        "project": "Project",
        "interaction": "Interaction",
    },
    "leads": {
        "statuses": ["open", "qualified", "proposal", "won", "lost"],
        "statusConfig": {
            "colors": {
                "open": "bg-yellow-100 text-yellow-800",
                "qualified": "bg-orange-100 text-orange-800",
                "proposal": "bg-blue-100 text-blue-800",
                "won": "bg-green-100 text-green-800",
                "lost": "bg-red-100 text-red-800"
            },
            "icons": {
                "open": "circle-yellow",
                "qualified": "circle-orange",
                "proposal": "circle-blue",
                "won": "circle-green",
                "lost": "circle-red"
            },
            "labels": {
                "open": "Open",
                "qualified": "Qualified",
                "proposal": "Proposal",
                "won": "Won",
                "lost": "Lost"
            }
        },
        "sources": [
            "Website", "Referral", "Cold Call", "Email Campaign",
            "Social Media", "Trade Show", "Advertisement", "Partner", "Other"
        ],
        "temperatures": ["hot", "warm", "cold"],
        "temperatureConfig": {
            "colors": {
                "hot": "text-red-600",
                "warm": "text-orange-600",
                "cold": "text-blue-600"
            },
            "icons": {
                "hot": "fire",
                "warm": "sun",
                "cold": "snowflake"
            }
        }
    },
    "businessTypes": [
        "None", "Oil & Gas", "Secondary Containment", "Tanks", "Pipe",
        "Rental", "Food and Beverage", "Bridge", "Culvert"
    ],
    "regional": {
        "currency": "USD",
        "currencySymbol": "$",
        "dateFormat": "MM/DD/YYYY",
        "phoneFormat": "US",
        "addressFormat": "US"
    },
    "features": {
        "showTemperature": True,
        "showLeadScore": False,
        "showSource": True,
        "enableProjectStandalone": True,
        "enableBulkOperations": True,
        "enableAdvancedFilters": True,
        "enableDataExport": True
    },
    "defaults": {
        "leadsPerPage": 10,
        "clientsPerPage": 10,
        "projectsPerPage": 10,
        "defaultView": "cards",
        "defaultSort": "newest"
    }
}


def upgrade():
    """Create tenants table and migrate existing tenant #1"""

    # Step 1: Create tenants table
    op.create_table(
        'tenants',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('slug', sa.String(50), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('config', sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )

    # Create indexes
    op.create_index('ix_tenants_slug', 'tenants', ['slug'], unique=True)

    # Step 2: Insert tenant #1 (ASFI) - MUST happen before FK constraint
    # Using raw SQL to insert with explicit id=1
    op.execute(
        sa.text("""
            INSERT INTO tenants (id, name, slug, is_active, config)
            VALUES (1, 'ASFI', 'asfi', true, :config)
        """).bindparams(config=json.dumps(ASFI_CONFIG))
    )

    # Reset the sequence so next tenant gets id=2
    # This is PostgreSQL-specific
    op.execute(sa.text("SELECT setval('tenants_id_seq', 1, true)"))

    # Step 3: Add FK constraint from users.tenant_id -> tenants.id
    # Now safe because tenant #1 exists
    op.create_foreign_key(
        'fk_users_tenant_id',
        'users', 'tenants',
        ['tenant_id'], ['id']
    )


def downgrade():
    """Remove tenants table and FK constraint"""

    # Drop FK constraint first
    op.drop_constraint('fk_users_tenant_id', 'users', type_='foreignkey')

    # Drop tenants table
    op.drop_index('ix_tenants_slug', table_name='tenants')
    op.drop_table('tenants')
