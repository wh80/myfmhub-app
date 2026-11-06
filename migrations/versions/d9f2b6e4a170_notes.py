"""notes

Revision ID: d9f2b6e4a170
Revises: c4e1a7d9b352
Create Date: 2026-11-06 20:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'd9f2b6e4a170'
down_revision = 'c4e1a7d9b352'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'note',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('is_system', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('created_by_name', sa.String(length=201), nullable=True),
        sa.Column('organisation_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('job_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_by_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(['created_by_id'], ['person.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['job_id'], ['job.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['organisation_id'], ['organisation.id']),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade():
    op.drop_table('note')
