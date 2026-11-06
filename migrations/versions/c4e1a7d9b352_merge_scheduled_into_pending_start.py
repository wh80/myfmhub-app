"""merge scheduled into pending_start

A job waiting for its date now waits at pending_start, like one waiting for the assignee to confirm the start. Jobs
that can't move on by date (imported at these statuses, so with no date) now need start confirmation, or they'd have
no way out of pending_start. That flag isn't put back on a downgrade.

Revision ID: c4e1a7d9b352
Revises: f7a1c5e3d826
Create Date: 2026-11-06 18:00:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'c4e1a7d9b352'
down_revision = 'f7a1c5e3d826'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('job', schema=None) as batch_op:
        batch_op.drop_constraint('ck_job_status', type_='check')

    op.execute("UPDATE job SET status = 'pending_start' WHERE status = 'scheduled'")
    op.execute("UPDATE job SET require_start_confirmation = true WHERE status = 'pending_start' AND scheduled_for IS NULL")

    with op.batch_alter_table('job', schema=None) as batch_op:
        batch_op.create_check_constraint(
            'ck_job_status',
            "status IN ('new', 'unassigned', 'pending_acceptance', 'pending_scheduling', 'pending_start', "
            "'in_progress', 'completed', 'closed', 'rejected')",
        )


def downgrade():
    with op.batch_alter_table('job', schema=None) as batch_op:
        batch_op.drop_constraint('ck_job_status', type_='check')

    op.execute("UPDATE job SET status = 'scheduled' WHERE status = 'pending_start' AND NOT require_start_confirmation")

    with op.batch_alter_table('job', schema=None) as batch_op:
        batch_op.create_check_constraint(
            'ck_job_status',
            "status IN ('new', 'unassigned', 'pending_acceptance', 'pending_scheduling', 'scheduled', 'pending_start', "
            "'in_progress', 'completed', 'closed', 'rejected')",
        )
