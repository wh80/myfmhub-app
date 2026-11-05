"""job assigned by

Revision ID: c2d84b7e5a31
Revises: a7c3e91d2f10
Create Date: 2026-11-05 16:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c2d84b7e5a31'
down_revision = 'a7c3e91d2f10'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('job', schema=None) as batch_op:
        batch_op.add_column(sa.Column('assigned_by_id', sa.UUID(), nullable=True))
        batch_op.create_foreign_key('job_assigned_by_id_fkey', 'person', ['assigned_by_id'], ['id'], ondelete='SET NULL')


def downgrade():
    with op.batch_alter_table('job', schema=None) as batch_op:
        batch_op.drop_constraint('job_assigned_by_id_fkey', type_='foreignkey')
        batch_op.drop_column('assigned_by_id')
