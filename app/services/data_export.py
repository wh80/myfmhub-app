import csv
import io
import zipfile
from collections import namedtuple

from app.extensions import db
from app.models import Asset, Job, JobSchedule, Location, Person, Supplier
from app.services.exports import csv_value
from app.services.auth import as_utc
from app.services.scheduling import UK_TIMEZONE

# the "Export data" page: an organisation downloads all of its records as a zip of CSVs, one per type.
# Unlike the index downloads, every field is included, with ids so records can be matched up again
# (a job's Location ID, Job Schedule ID and Asset IDs). Users aren't exported, only the people records.
# Dates and times are as stored, so created and updated are UTC

# permission is the view permission needed to export the type, on top of exports.manage. options
# returns the loader options, as a function because backrefs like Asset.location don't exist until the mappers are configured
ExportType = namedtuple('ExportType', 'key label model permission columns options')


def ids(records):
    return '; '.join(sorted(str(record.id) for record in records))


# a time someone entered in UK time (like a job's completed_at), exported in UK time
def as_uk(value):
    return as_utc(value).astimezone(UK_TIMEZONE) if value else None


ADDRESS_COLUMNS = [
    ('Address', lambda record: record.address),
    ('Telephone', lambda record: record.telephone),
]
# a record's location, by its path
LOCATION_COLUMNS = [
    ('Location', lambda record: record.location.materialised_path),
    ('Location ID', lambda record: record.location_id),
]
# a category or condition (services/reference_data.py): its description, and its id
def reference_columns(label, attribute):
    return [
        (label, lambda record: getattr(record, attribute).description if getattr(record, attribute) else None),
        (f'{label} ID', lambda record: getattr(record, f'{attribute}_id')),
    ]
TIMESTAMP_COLUMNS = [
    ('Created (UTC)', lambda record: record.created_at),
    ('Updated (UTC)', lambda record: record.updated_at),
]

EXPORT_TYPES = {export_type.key: export_type for export_type in [
    ExportType('locations', 'Locations', Location, 'locations.view', [
        ('ID', lambda location: location.id),
        ('Name', lambda location: location.name),
        ('Path', lambda location: location.materialised_path),
        ('Parent Location ID', lambda location: location.parent_id),
        *reference_columns('Location Category', 'location_category'),
        ('Main Contact', lambda location: location.main_contact.full_name if location.main_contact else None),
        ('Main Contact ID', lambda location: location.main_contact_id),
        *ADDRESS_COLUMNS,
        *TIMESTAMP_COLUMNS,
    ], lambda: []),
    ExportType('assets', 'Assets', Asset, 'assets.view', [
        ('ID', lambda asset: asset.id),
        ('Description', lambda asset: asset.description),
        *LOCATION_COLUMNS,
        ('Asset Number', lambda asset: asset.asset_number),
        ('Make', lambda asset: asset.make),
        ('Model', lambda asset: asset.model),
        ('Serial Number', lambda asset: asset.serial_number),
        ('Purchase Date', lambda asset: asset.purchase_date),
        ('Purchased From', lambda asset: asset.purchased_from),
        ('Warranty Expiry', lambda asset: asset.warranty_expiry),
        ('Est. Replacement Date', lambda asset: asset.estimated_replacement_date),
        ('Est. Replacement Cost', lambda asset: asset.estimated_replacement_cost),
        ('Assigned To', lambda asset: f'{asset.assigned_to.first_name} {asset.assigned_to.last_name}' if asset.assigned_to else None),
        ('Assigned To ID', lambda asset: asset.assigned_to_id),
        ('Part Of Asset ID', lambda asset: asset.parent_id),
        *reference_columns('Asset Category', 'asset_category'),
        *reference_columns('Asset Condition', 'asset_condition'),
        ('Condition Set', lambda asset: asset.condition_set),
        *TIMESTAMP_COLUMNS,
    ], lambda: [db.joinedload(Asset.location)]),
    ExportType('jobs', 'Jobs', Job, 'jobs.view', [
        ('ID', lambda job: job.id),
        ('Description', lambda job: job.description),
        ('Work Required', lambda job: job.work_required),
        *LOCATION_COLUMNS,
        ('Due By', lambda job: job.due_by),
        ('Source', lambda job: job.source_label),
        ('Status', lambda job: job.status_label),
        ('Assigned To', lambda job: job.assignee_name),
        ('Assigned Supplier ID', lambda job: job.assigned_supplier_id),
        ('Assigned Person ID', lambda job: job.assigned_person_id),
        ('Helpers', lambda job: '; '.join(f'{person.first_name} {person.last_name}' for person in job.helpers) or None),
        ('Helper IDs', lambda job: ids(job.helpers) or None),
        ('Approved At (UTC)', lambda job: job.approved_at),
        ('Rejected At (UTC)', lambda job: job.rejected_at),
        ('Assigned At (UTC)', lambda job: job.assigned_at),
        ('Started At (UK)', lambda job: as_uk(job.started_at)),
        ('Completed At (UK)', lambda job: as_uk(job.completed_at)),
        ('Completion Comments', lambda job: job.completion_comments),
        ('Closed On', lambda job: job.closed_at),
        ('Closed By', lambda job: f'{job.closed_by.first_name} {job.closed_by.last_name}' if job.closed_by else None),
        ('Closed By ID', lambda job: job.closed_by_id),
        ('Rejection Reason', lambda job: job.rejection_reason),
        ('Statutory', lambda job: job.statutory_compliance),
        ('Job Schedule ID', lambda job: job.job_schedule_id),
        ('Parent Job ID', lambda job: job.parent_job_id),
        *reference_columns('Job Category', 'job_category'),
        ('Estimated Cost', lambda job: job.estimated_cost),
        ('Actual Cost', lambda job: job.actual_cost),
        ('Asset IDs', lambda job: ids(job.assets)),
        *TIMESTAMP_COLUMNS,
    ], lambda: [db.joinedload(Job.location), db.selectinload(Job.assets)]),
    ExportType('job_schedules', 'Job Schedules', JobSchedule, 'job_schedules.view', [
        ('ID', lambda job_schedule: job_schedule.id),
        ('Description', lambda job_schedule: job_schedule.description),
        ('Work Required', lambda job_schedule: job_schedule.work_required),
        *LOCATION_COLUMNS,
        ('Frequency', lambda job_schedule: job_schedule.frequency_label),
        ('Next Due By', lambda job_schedule: job_schedule.next_due_by),
        ('Notice Days', lambda job_schedule: job_schedule.notice_days),
        ('Next Job Creation', lambda job_schedule: job_schedule.next_job_creation_date),
        ('Statutory', lambda job_schedule: job_schedule.statutory_compliance),
        ('Active', lambda job_schedule: job_schedule.is_active),
        *reference_columns('Job Category', 'job_category'),
        ('Estimated Cost', lambda job_schedule: job_schedule.estimated_cost),
        ('Estimated Duration (days)', lambda job_schedule: job_schedule.estimated_duration),
        ('Asset IDs', lambda job_schedule: ids(job_schedule.assets)),
        *TIMESTAMP_COLUMNS,
    ], lambda: [db.joinedload(JobSchedule.location), db.selectinload(JobSchedule.assets)]),
    ExportType('suppliers', 'Suppliers', Supplier, 'suppliers.view', [
        ('ID', lambda supplier: supplier.id),
        ('Name', lambda supplier: supplier.name),
        *ADDRESS_COLUMNS,
        ('Email', lambda supplier: supplier.email),
        *reference_columns('Supplier Category', 'supplier_category'),
        *TIMESTAMP_COLUMNS,
    ], lambda: []),
    # the person record, including the two settings the organisation controls on it. Nothing from the user (login)
    ExportType('people', 'People', Person, 'people.view', [
        ('ID', lambda person: person.id),
        ('First Name', lambda person: person.first_name),
        ('Last Name', lambda person: person.last_name),
        ('Email', lambda person: person.email),
        ('Job Title', lambda person: person.job_title),
        ('Mobile', lambda person: person.mobile),
        ('User Group', lambda person: person.user_group.name if person.user_group else None),
        ('Can Log In', lambda person: person.can_login),
        *TIMESTAMP_COLUMNS,
    ], lambda: [db.joinedload(Person.user_group)]),
]}


def export_csv(export_type, records):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([label for label, _ in export_type.columns])
    for record in records:
        writer.writerow([csv_value(getter(record)) for _, getter in export_type.columns])
    # the BOM makes Excel read the file as UTF-8
    return '﻿' + output.getvalue()


def export_zip(files):
    """files is a list of (export_type, records). Returns the zip's bytes."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for export_type, records in files:
            archive.writestr(f"{export_type.key.replace('_', '-')}.csv", export_csv(export_type, records).encode('utf-8'))
    return buffer.getvalue()
