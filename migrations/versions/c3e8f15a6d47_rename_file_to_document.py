"""rename file to document

Written by hand: autogenerate sees a rename as a drop and a create, which would lose the data. Constraints
are renamed too, so the database matches one built from a regenerated initial migration. Stored permission
keys are the permission resource, which is now documents. storage_key is unchanged, so nothing moves in
storage.

Revision ID: c3e8f15a6d47
Revises: a7c41e9d2b30
Create Date: 2026-11-04 11:00:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'c3e8f15a6d47'
down_revision = 'a7c41e9d2b30'
branch_labels = None
depends_on = None


TABLES = [
    ('file', 'document'),
    ('file_category', 'document_category'),
    ('file_export', 'document_export'),
]
# (link table, the column for the record it links to)
LINK_TABLES = [
    ('location', 'location_id'),
    ('job', 'job_id'),
    ('asset', 'asset_id'),
    ('planned_job_schedule', 'planned_job_schedule_id'),
    ('person', 'person_id'),
    ('supplier', 'supplier_id'),
]
# (table by its new name, old constraint, new constraint)
CONSTRAINTS = [
    ('document', 'file_pkey', 'document_pkey'),
    ('document', 'file_storage_key_key', 'document_storage_key_key'),
    ('document', 'file_organisation_id_fkey', 'document_organisation_id_fkey'),
    ('document', 'file_file_category_id_fkey', 'document_document_category_id_fkey'),
    ('document_category', 'file_category_pkey', 'document_category_pkey'),
    ('document_category', 'file_category_organisation_id_fkey', 'document_category_organisation_id_fkey'),
    ('document_category', 'file_category_organisation_id_description_key', 'document_category_organisation_id_description_key'),
    ('document_export', 'file_export_pkey', 'document_export_pkey'),
    ('document_export', 'file_export_organisation_id_fkey', 'document_export_organisation_id_fkey'),
    ('document_export', 'file_export_person_id_fkey', 'document_export_person_id_fkey'),
    ('document_export', 'ck_file_export_status', 'ck_document_export_status'),
]
for record, column in LINK_TABLES:
    CONSTRAINTS += [
        (f'{record}_documents', f'{record}_files_pkey', f'{record}_documents_pkey'),
        (f'{record}_documents', f'{record}_files_{column}_fkey', f'{record}_documents_{column}_fkey'),
        (f'{record}_documents', f'{record}_files_file_id_fkey', f'{record}_documents_document_id_fkey'),
    ]


def upgrade():
    op.alter_column('file', 'file_category_id', new_column_name='document_category_id')
    op.alter_column('file_export', 'file_count', new_column_name='document_count')
    for old, new in TABLES:
        op.rename_table(old, new)
    for record, _ in LINK_TABLES:
        op.alter_column(f'{record}_files', 'file_id', new_column_name='document_id')
        op.rename_table(f'{record}_files', f'{record}_documents')
    for table, old, new in CONSTRAINTS:
        op.execute(f'ALTER TABLE {table} RENAME CONSTRAINT {old} TO {new}')

    op.execute("UPDATE user_group_permission SET permission = 'documents.' || substr(permission, length('files.') + 1) "
               "WHERE permission LIKE 'files.%'")


def downgrade():
    op.execute("UPDATE user_group_permission SET permission = 'files.' || substr(permission, length('documents.') + 1) "
               "WHERE permission LIKE 'documents.%'")

    for table, old, new in CONSTRAINTS:
        op.execute(f'ALTER TABLE {table} RENAME CONSTRAINT {new} TO {old}')
    for record, _ in LINK_TABLES:
        op.rename_table(f'{record}_documents', f'{record}_files')
        op.alter_column(f'{record}_files', 'document_id', new_column_name='file_id')
    for old, new in TABLES:
        op.rename_table(new, old)
    op.alter_column('file_export', 'document_count', new_column_name='file_count')
    op.alter_column('file', 'document_category_id', new_column_name='file_category_id')
