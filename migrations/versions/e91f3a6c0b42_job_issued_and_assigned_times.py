"""job issued and assigned times

Revision ID: e91f3a6c0b42
Revises: c2d84b7e5a31
Create Date: 2026-11-05 17:00:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'e91f3a6c0b42'
down_revision = 'c2d84b7e5a31'
branch_labels = None
depends_on = None


def upgrade():
    # issued_at is now set whenever a job is given to someone, and assigned_at only once that's taken effect
    op.execute("UPDATE job SET issued_at = assigned_at WHERE issued_at IS NULL AND assigned_at IS NOT NULL")
    op.execute("UPDATE job SET assigned_at = NULL WHERE status = 'issued'")


def downgrade():
    op.execute("UPDATE job SET assigned_at = issued_at WHERE status = 'issued'")
    op.execute("UPDATE job SET issued_at = NULL WHERE status <> 'issued'")
