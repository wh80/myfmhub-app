"""add job and job schedule costs

Revision ID: b4e7d2c91f35
Revises: 3965cc9e40a2
Create Date: 2026-10-28 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b4e7d2c91f35'
down_revision = '3965cc9e40a2'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('job_schedule', schema=None) as batch_op:
        batch_op.add_column(sa.Column('estimated_cost', sa.Numeric(precision=12, scale=2), nullable=True))
        batch_op.add_column(sa.Column('estimated_duration', sa.Numeric(precision=6, scale=2), nullable=True))

    with op.batch_alter_table('job', schema=None) as batch_op:
        batch_op.add_column(sa.Column('estimated_cost', sa.Numeric(precision=12, scale=2), nullable=True))
        batch_op.add_column(sa.Column('actual_cost', sa.Numeric(precision=12, scale=2), nullable=True))


def downgrade():
    with op.batch_alter_table('job', schema=None) as batch_op:
        batch_op.drop_column('actual_cost')
        batch_op.drop_column('estimated_cost')

    with op.batch_alter_table('job_schedule', schema=None) as batch_op:
        batch_op.drop_column('estimated_duration')
        batch_op.drop_column('estimated_cost')
