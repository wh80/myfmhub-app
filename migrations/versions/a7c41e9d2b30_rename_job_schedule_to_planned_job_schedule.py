"""rename job schedule to planned job schedule

Written by hand: autogenerate sees a rename as a drop and a create, which would lose the data. Constraints
are renamed too, so the database matches one built from a regenerated initial migration. Stored permission
keys and import types are the permission resource, which is now planned_jobs.

Revision ID: a7c41e9d2b30
Revises: d267988fe376
Create Date: 2026-11-04 10:00:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'a7c41e9d2b30'
down_revision = 'd267988fe376'
branch_labels = None
depends_on = None


TABLES = [
    ('job_schedule', 'planned_job_schedule'),
    ('job_schedule_assets', 'planned_job_schedule_assets'),
    ('job_schedule_files', 'planned_job_schedule_files'),
]
# (table, old column, new column), table by its old name
COLUMNS = [
    ('job', 'job_schedule_id', 'planned_job_schedule_id'),
    ('job_schedule_assets', 'job_schedule_id', 'planned_job_schedule_id'),
    ('job_schedule_files', 'job_schedule_id', 'planned_job_schedule_id'),
]
# (table by its new name, old constraint, new constraint). uq_job_open_per_schedule is still right
CONSTRAINTS = [
    ('planned_job_schedule', 'job_schedule_pkey', 'planned_job_schedule_pkey'),
    ('planned_job_schedule', 'job_schedule_import_event_id_fkey', 'planned_job_schedule_import_event_id_fkey'),
    ('planned_job_schedule', 'job_schedule_job_category_id_fkey', 'planned_job_schedule_job_category_id_fkey'),
    ('planned_job_schedule', 'job_schedule_location_id_fkey', 'planned_job_schedule_location_id_fkey'),
    ('planned_job_schedule', 'job_schedule_organisation_id_fkey', 'planned_job_schedule_organisation_id_fkey'),
    ('job', 'job_job_schedule_id_fkey', 'job_planned_job_schedule_id_fkey'),
    ('planned_job_schedule_assets', 'job_schedule_assets_pkey', 'planned_job_schedule_assets_pkey'),
    ('planned_job_schedule_assets', 'job_schedule_assets_asset_id_fkey', 'planned_job_schedule_assets_asset_id_fkey'),
    ('planned_job_schedule_assets', 'job_schedule_assets_job_schedule_id_fkey', 'planned_job_schedule_assets_planned_job_schedule_id_fkey'),
    ('planned_job_schedule_files', 'job_schedule_files_pkey', 'planned_job_schedule_files_pkey'),
    ('planned_job_schedule_files', 'job_schedule_files_file_id_fkey', 'planned_job_schedule_files_file_id_fkey'),
    ('planned_job_schedule_files', 'job_schedule_files_job_schedule_id_fkey', 'planned_job_schedule_files_planned_job_schedule_id_fkey'),
]


def upgrade():
    for table, old, new in COLUMNS:
        op.alter_column(table, old, new_column_name=new)
    for old, new in TABLES:
        op.rename_table(old, new)
    for table, old, new in CONSTRAINTS:
        op.execute(f'ALTER TABLE {table} RENAME CONSTRAINT {old} TO {new}')

    op.execute("UPDATE user_group_permission SET permission = 'planned_jobs.' || substr(permission, length('job_schedules.') + 1) "
               "WHERE permission LIKE 'job\_schedules.%'")
    op.execute("UPDATE import_event SET import_type = 'planned_jobs' WHERE import_type = 'job_schedules'")


def downgrade():
    op.execute("UPDATE import_event SET import_type = 'job_schedules' WHERE import_type = 'planned_jobs'")
    op.execute("UPDATE user_group_permission SET permission = 'job_schedules.' || substr(permission, length('planned_jobs.') + 1) "
               "WHERE permission LIKE 'planned\_jobs.%'")

    for table, old, new in CONSTRAINTS:
        op.execute(f'ALTER TABLE {table} RENAME CONSTRAINT {new} TO {old}')
    for old, new in TABLES:
        op.rename_table(new, old)
    for table, old, new in COLUMNS:
        op.alter_column(table, new, new_column_name=old)
