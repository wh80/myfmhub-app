"""job scheduling status and scheduled_for

Revision ID: d3f6a8b2c014
Revises: b5e07d1c9f23
Create Date: 2026-11-05 19:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd3f6a8b2c014'
down_revision = 'b5e07d1c9f23'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('job', schema=None) as batch_op:
        batch_op.add_column(sa.Column('require_scheduling', sa.Boolean(), server_default=sa.text('false'), nullable=False))
        batch_op.add_column(sa.Column('scheduled_for', sa.Date(), nullable=True))
        batch_op.drop_constraint('ck_job_status', type_='check')
        batch_op.create_check_constraint(
            'ck_job_status',
            "status IN ('new', 'unassigned', 'issued', 'scheduling', 'assigned', 'started', 'completed', 'closed', 'rejected')",
        )


def downgrade():
    # a job waiting for a date has an assignee, so assigned is the nearest status
    op.execute("UPDATE job SET status = 'assigned' WHERE status = 'scheduling'")
    with op.batch_alter_table('job', schema=None) as batch_op:
        batch_op.drop_constraint('ck_job_status', type_='check')
        batch_op.create_check_constraint(
            'ck_job_status',
            "status IN ('new', 'unassigned', 'issued', 'assigned', 'started', 'completed', 'closed', 'rejected')",
        )
        batch_op.drop_column('scheduled_for')
        batch_op.drop_column('require_scheduling')
