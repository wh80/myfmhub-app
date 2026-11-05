"""job issued status and issued_at

Revision ID: a7c3e91d2f10
Revises: f40cbd248237
Create Date: 2026-11-05 15:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a7c3e91d2f10'
down_revision = 'f40cbd248237'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('job', schema=None) as batch_op:
        batch_op.add_column(sa.Column('issued_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.drop_constraint('ck_job_status', type_='check')
        batch_op.create_check_constraint(
            'ck_job_status',
            "status IN ('new', 'unassigned', 'issued', 'assigned', 'in_progress', 'completed', 'closed', 'rejected')",
        )


def downgrade():
    # issued jobs have an assignee, so assigned is the nearest status without one to accept
    op.execute("UPDATE job SET status = 'assigned' WHERE status = 'issued'")
    with op.batch_alter_table('job', schema=None) as batch_op:
        batch_op.drop_constraint('ck_job_status', type_='check')
        batch_op.create_check_constraint(
            'ck_job_status',
            "status IN ('new', 'unassigned', 'assigned', 'in_progress', 'completed', 'closed', 'rejected')",
        )
        batch_op.drop_column('issued_at')
