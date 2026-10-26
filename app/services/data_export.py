import csv
import io
import zipfile
from collections import namedtuple

from app.extensions import db
from app.models import Asset, Building, Job, JobSchedule, Person, Site, Supplier, User, Zone
from app.services.exports import csv_value

# the "Export data" page: an organisation downloads all of its records as a zip of CSVs, one per type.
# Unlike the index downloads, every field is included, with ids so records can be matched up again
# (a job's Site ID, Job Schedule ID and Asset IDs). Users aren't exported, only the people records.
# Dates and times are as stored, so created and updated are UTC

# permission is the view permission needed to export the type, on top of exports.manage. options
# returns the loader options, as a function because backrefs like Asset.site don't exist until the mappers are configured
ExportType = namedtuple('ExportType', 'key label model permission columns options')


def ids(records):
    return '; '.join(sorted(str(record.id) for record in records))


ADDRESS_COLUMNS = [
    ('Address Line One', lambda record: record.address_line_one),
    ('Address Line Two', lambda record: record.address_line_two),
    ('Town/City', lambda record: record.town_city),
    ('Post Code', lambda record: record.post_code),
    ('Telephone', lambda record: record.telephone),
]
# a record's optional building and zone, after its site
LOCATION_COLUMNS = [
    ('Building', lambda record: record.building.name if record.building else None),
    ('Building ID', lambda record: record.building_id),
    ('Zone', lambda record: record.zone.name if record.zone else None),
    ('Zone ID', lambda record: record.zone_id),
]
TIMESTAMP_COLUMNS = [
    ('Created (UTC)', lambda record: record.created_at),
    ('Updated (UTC)', lambda record: record.updated_at),
]

EXPORT_TYPES = {export_type.key: export_type for export_type in [
    ExportType('sites', 'Sites', Site, 'sites.view', [
        ('ID', lambda site: site.id),
        ('Name', lambda site: site.name),
        *ADDRESS_COLUMNS,
        *TIMESTAMP_COLUMNS,
    ], lambda: []),
    ExportType('buildings', 'Buildings', Building, 'buildings.view', [
        ('ID', lambda building: building.id),
        ('Name', lambda building: building.name),
        ('Site', lambda building: building.site.name),
        ('Site ID', lambda building: building.site_id),
        *TIMESTAMP_COLUMNS,
    ], lambda: [db.joinedload(Building.site)]),
    ExportType('zones', 'Zones', Zone, 'zones.view', [
        ('ID', lambda zone: zone.id),
        ('Name', lambda zone: zone.name),
        ('Building', lambda zone: zone.building.name),
        ('Building ID', lambda zone: zone.building_id),
        ('Site', lambda zone: zone.building.site.name),
        ('Site ID', lambda zone: zone.building.site_id),
        *TIMESTAMP_COLUMNS,
    ], lambda: [db.joinedload(Zone.building).joinedload(Building.site)]),
    ExportType('assets', 'Assets', Asset, 'assets.view', [
        ('ID', lambda asset: asset.id),
        ('Description', lambda asset: asset.description),
        ('Site', lambda asset: asset.site.name),
        ('Site ID', lambda asset: asset.site_id),
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
        *TIMESTAMP_COLUMNS,
    ], lambda: [db.joinedload(Asset.site)]),
    ExportType('jobs', 'Jobs', Job, 'jobs.view', [
        ('ID', lambda job: job.id),
        ('Description', lambda job: job.description),
        ('Work Required', lambda job: job.work_required),
        ('Site', lambda job: job.site.name),
        ('Site ID', lambda job: job.site_id),
        *LOCATION_COLUMNS,
        ('Due By', lambda job: job.due_by),
        ('Source', lambda job: job.source_label),
        ('Status', lambda job: job.status_label),
        ('Closed On', lambda job: job.closed_at),
        ('Statutory', lambda job: job.statutory_compliance),
        ('Job Schedule ID', lambda job: job.job_schedule_id),
        ('Asset IDs', lambda job: ids(job.assets)),
        *TIMESTAMP_COLUMNS,
    ], lambda: [db.joinedload(Job.site), db.selectinload(Job.assets)]),
    ExportType('job_schedules', 'Job Schedules', JobSchedule, 'job_schedules.view', [
        ('ID', lambda job_schedule: job_schedule.id),
        ('Description', lambda job_schedule: job_schedule.description),
        ('Work Required', lambda job_schedule: job_schedule.work_required),
        ('Site', lambda job_schedule: job_schedule.site.name),
        ('Site ID', lambda job_schedule: job_schedule.site_id),
        *LOCATION_COLUMNS,
        ('Frequency', lambda job_schedule: job_schedule.frequency_label),
        ('Next Due By', lambda job_schedule: job_schedule.next_due_by),
        ('Notice Days', lambda job_schedule: job_schedule.notice_days),
        ('Next Job Creation', lambda job_schedule: job_schedule.next_job_creation_date),
        ('Statutory', lambda job_schedule: job_schedule.statutory_compliance),
        ('Active', lambda job_schedule: job_schedule.is_active),
        ('Asset IDs', lambda job_schedule: ids(job_schedule.assets)),
        *TIMESTAMP_COLUMNS,
    ], lambda: [db.joinedload(JobSchedule.site), db.selectinload(JobSchedule.assets)]),
    ExportType('suppliers', 'Suppliers', Supplier, 'suppliers.view', [
        ('ID', lambda supplier: supplier.id),
        ('Name', lambda supplier: supplier.name),
        *ADDRESS_COLUMNS,
        *TIMESTAMP_COLUMNS,
    ], lambda: []),
    # the person record plus the two settings the organisation controls on it. Nothing else from the user
    ExportType('people', 'People', Person, 'people.view', [
        ('ID', lambda person: person.id),
        ('First Name', lambda person: person.first_name),
        ('Last Name', lambda person: person.last_name),
        ('Email', lambda person: person.email),
        ('Job Title', lambda person: person.job_title),
        ('Mobile', lambda person: person.mobile),
        ('User Group', lambda person: person.user.user_group.name if person.user and person.user.user_group else None),
        ('Can Log In', lambda person: bool(person.user and person.user.can_login)),
        *TIMESTAMP_COLUMNS,
    ], lambda: [db.joinedload(Person.user).joinedload(User.user_group)]),
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
