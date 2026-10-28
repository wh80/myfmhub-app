"""add site main contact

Revision ID: d2b5e8f04a17
Revises: c8a1f6e3d920
Create Date: 2026-10-28 20:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd2b5e8f04a17'
down_revision = 'c8a1f6e3d920'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('site', schema=None) as batch_op:
        batch_op.add_column(sa.Column('main_contact_id', sa.UUID(), nullable=True))
        batch_op.create_foreign_key('site_main_contact_id_fkey', 'person', ['main_contact_id'], ['id'], ondelete='SET NULL')


def downgrade():
    with op.batch_alter_table('site', schema=None) as batch_op:
        batch_op.drop_constraint('site_main_contact_id_fkey', type_='foreignkey')
        batch_op.drop_column('main_contact_id')
