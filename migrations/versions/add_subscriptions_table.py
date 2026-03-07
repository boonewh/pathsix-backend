"""Add subscriptions table

Revision ID: add_subscriptions_table
Revises: add_backup_tables
Create Date: 2026-03-06

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_subscriptions_table'
down_revision = 'add_backup_tables'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'subscriptions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('client_id', sa.Integer(), sa.ForeignKey('clients.id'), nullable=False),
        sa.Column('plan_name', sa.String(255), nullable=False),
        sa.Column('price', sa.Float(), nullable=False),
        sa.Column('billing_cycle', sa.String(20), nullable=False),
        sa.Column('start_date', sa.DateTime(), nullable=False),
        sa.Column('renewal_date', sa.DateTime(), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='active'),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('updated_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('cancelled_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_subscriptions_tenant_id', 'subscriptions', ['tenant_id'])
    op.create_index('ix_subscriptions_client_id', 'subscriptions', ['client_id'])
    op.create_index('ix_subscriptions_renewal_date', 'subscriptions', ['renewal_date'])


def downgrade():
    op.drop_index('ix_subscriptions_renewal_date', table_name='subscriptions')
    op.drop_index('ix_subscriptions_client_id', table_name='subscriptions')
    op.drop_index('ix_subscriptions_tenant_id', table_name='subscriptions')
    op.drop_table('subscriptions')
