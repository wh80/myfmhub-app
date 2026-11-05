"""job started status and start confirmation

Revision ID: b5e07d1c9f23
Revises: e91f3a6c0b42
Create Date: 2026-11-05 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b5e07d1c9f23'
down_revision = 'e91f3a6c0b42'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('job', schema=None) as batch_op:
        batch_op.add_column(sa.Column('require_start_confirmation', sa.Boolean(), server_default=sa.text('false'), nullable=False))
        batch_op.drop_constraint('ck_job_status', type_='check')

    op.execute("UPDATE job SET status = 'started' WHERE status = 'in_progress'")

    with op.batch_alter_table('job', schema=None) as batch_op:
        batch_op.create_check_constraint(
            'ck_job_status',
            "status IN ('new', 'unassigned', 'issued', 'assigned', 'started', 'completed', 'closed', 'rejected')",
        )


def downgrade():
    with op.batch_alter_table('job', schema=None) as batch_op:
        batch_op.drop_constraint('ck_job_status', type_='check')

    op.execute("UPDATE job SET status = 'in_progress' WHERE status = 'started'")

    with op.batch_alter_table('job', schema=None) as batch_op:
        batch_op.create_check_constraint(
            'ck_job_status',
            "status IN ('new', 'unassigned', 'issued', 'assigned', 'in_progress', 'completed', 'closed', 'rejected')",
        )
        batch_op.drop_column('require_start_confirmation')
