"""job status rework

Replaces assigned and started with in_progress and the pending statuses. A job with its assignee is either
waiting on a named step or in progress.

Revision ID: f7a1c5e3d826
Revises: d3f6a8b2c014
Create Date: 2026-11-05 20:00:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'f7a1c5e3d826'
down_revision = 'd3f6a8b2c014'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('job', schema=None) as batch_op:
        batch_op.drop_constraint('ck_job_status', type_='check')

    op.execute("UPDATE job SET status = 'pending_acceptance' WHERE status = 'issued'")
    op.execute("UPDATE job SET status = 'pending_scheduling' WHERE status = 'scheduling'")
    op.execute("UPDATE job SET status = 'in_progress' WHERE status = 'started'")
    op.execute("UPDATE job SET status = 'pending_start' WHERE status = 'assigned' AND require_start_confirmation")
    op.execute("UPDATE job SET status = 'scheduled' WHERE status = 'assigned' AND scheduled_for > CURRENT_DATE")
    op.execute("UPDATE job SET status = 'in_progress' WHERE status = 'assigned'")

    with op.batch_alter_table('job', schema=None) as batch_op:
        batch_op.create_check_constraint(
            'ck_job_status',
            "status IN ('new', 'unassigned', 'pending_acceptance', 'pending_scheduling', 'scheduled', 'pending_start', "
            "'in_progress', 'completed', 'closed', 'rejected')",
        )


def downgrade():
    with op.batch_alter_table('job', schema=None) as batch_op:
        batch_op.drop_constraint('ck_job_status', type_='check')

    op.execute("UPDATE job SET status = 'issued' WHERE status = 'pending_acceptance'")
    op.execute("UPDATE job SET status = 'scheduling' WHERE status = 'pending_scheduling'")
    op.execute("UPDATE job SET status = 'assigned' WHERE status IN ('scheduled', 'pending_start')")
    # only a job someone pressed Start on was started: the rest were assigned
    op.execute("UPDATE job SET status = 'started' WHERE status = 'in_progress' AND started_at IS NOT NULL")
    op.execute("UPDATE job SET status = 'assigned' WHERE status = 'in_progress'")

    with op.batch_alter_table('job', schema=None) as batch_op:
        batch_op.create_check_constraint(
            'ck_job_status',
            "status IN ('new', 'unassigned', 'issued', 'scheduling', 'assigned', 'started', 'completed', 'closed', 'rejected')",
        )
