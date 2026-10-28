"""add support tickets

Revision ID: c8a1f6e3d920
Revises: b4e7d2c91f35
Create Date: 2026-10-28 19:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c8a1f6e3d920'
down_revision = 'b4e7d2c91f35'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('support_ticket',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=20), server_default='open', nullable=False),
    sa.Column('organisation_id', sa.UUID(), nullable=False),
    sa.Column('created_by_id', sa.UUID(), nullable=True),
    sa.CheckConstraint("status IN ('open', 'closed')", name='ck_support_ticket_status'),
    sa.ForeignKeyConstraint(['created_by_id'], ['person.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['organisation_id'], ['organisation.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('support_ticket_note',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('support_ticket_id', sa.UUID(), nullable=False),
    sa.Column('created_by_id', sa.UUID(), nullable=True),
    sa.ForeignKeyConstraint(['created_by_id'], ['user.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['support_ticket_id'], ['support_ticket.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade():
    op.drop_table('support_ticket_note')
    op.drop_table('support_ticket')
