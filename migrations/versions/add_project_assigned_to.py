"""Add assigned_to column to projects table

Revision ID: add_project_assigned_to
Revises: add_lead_source_field
Create Date: 2026-04-06

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_project_assigned_to'
down_revision = 'add_lead_source_field'
branch_labels = None
depends_on = None


def upgrade():
    """Add assigned_to column to projects table"""
    op.add_column('projects', sa.Column('assigned_to', sa.Integer(), sa.ForeignKey('users.id'), nullable=True))
    op.create_index('ix_projects_assigned_to', 'projects', ['assigned_to'])


def downgrade():
    """Remove assigned_to column from projects table"""
    op.drop_index('ix_projects_assigned_to', table_name='projects')
    op.drop_column('projects', 'assigned_to')
