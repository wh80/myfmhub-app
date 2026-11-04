from app.extensions import db
import uuid
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declared_attr
from datetime import datetime, timezone
from app.services.scheduling import FREQUENCY_LABELS, uk_date, uk_today

# a job's lifecycle. The key is stored on Job.status, which is set by the app, never a form. A new job is
# approved (to unassigned) or rejected, then assigned to one supplier or person, who starts and completes
# it. Then it's closed
JOB_STATUSES = [
    ('new', 'New'),
    ('unassigned', 'Unassigned'),
    ('assigned', 'Assigned'),
    ('in_progress', 'In Progress'),
    ('completed', 'Completed'),
    ('closed', 'Closed'),
    ('rejected', 'Rejected'),
]
JOB_STATUS_LABELS = dict(JOB_STATUSES)
# statuses a job doesn't move on from: it isn't overdue, a compliance risk or "not closed", and can't be closed
JOB_FINISHED_STATUSES = ('closed', 'rejected')
# statuses where the job has an assignee: it can be reassigned and given helpers
ASSIGNED_JOB_STATUSES = ('assigned', 'in_progress', 'completed')

# where a job came from. The key is stored on Job.source, which is set by the app, never a form
JOB_SOURCES = [
    ('planned', 'Planned'),
    ('reactive', 'Reactive'),
    ('follow_up', 'Follow Up'),
]
JOB_SOURCE_LABELS = dict(JOB_SOURCES)

job_assets = db.Table(
    'job_assets',
    db.Column('job_id', UUID(as_uuid=True), db.ForeignKey('job.id', ondelete='CASCADE'), primary_key=True),
    db.Column('asset_id', UUID(as_uuid=True), db.ForeignKey('asset.id', ondelete='CASCADE'), primary_key=True),
)

# the people helping a job's assignee. Deleting either side removes only the link rows
job_helpers = db.Table(
    'job_helpers',
    db.Column('job_id', UUID(as_uuid=True), db.ForeignKey('job.id', ondelete='CASCADE'), primary_key=True),
    db.Column('person_id', UUID(as_uuid=True), db.ForeignKey('person.id', ondelete='CASCADE'), primary_key=True),
)

planned_job_schedule_assets = db.Table(
    'planned_job_schedule_assets',
    db.Column('planned_job_schedule_id', UUID(as_uuid=True), db.ForeignKey('planned_job_schedule.id', ondelete='CASCADE'), primary_key=True),
    db.Column('asset_id', UUID(as_uuid=True), db.ForeignKey('asset.id', ondelete='CASCADE'), primary_key=True),
)


# documents link to any number of these records, and each record to any number of documents. Deleting either
# side removes only the link rows; deleting a document is what removes it from storage (services/documents.py)
def document_link_table(name, column, target):
    return db.Table(
        name,
        db.Column(column, UUID(as_uuid=True), db.ForeignKey(f'{target}.id', ondelete='CASCADE'), primary_key=True),
        db.Column('document_id', UUID(as_uuid=True), db.ForeignKey('document.id', ondelete='CASCADE'), primary_key=True),
    )

location_documents = document_link_table('location_documents', 'location_id', 'location')
job_documents = document_link_table('job_documents', 'job_id', 'job')
asset_documents = document_link_table('asset_documents', 'asset_id', 'asset')
planned_job_schedule_documents = document_link_table('planned_job_schedule_documents', 'planned_job_schedule_id', 'planned_job_schedule')
person_documents = document_link_table('person_documents', 'person_id', 'person')
supplier_documents = document_link_table('supplier_documents', 'supplier_id', 'supplier')

class Organisation(db.Model):
    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    name = db.Column(db.String(100), nullable=False)
    # the organisation's own contact details, edited on My Organisation. One line per row, separated by
    # newlines, see services/addresses.py
    address = db.Column(db.String(500))
    telephone = db.Column(db.String(100))
    # set by a platform admin. Nobody can sign in to a suspended organisation
    suspended_at = db.Column(db.DateTime(timezone=True))
    # set when the person who registered it first enters a code. Anyone can register with any email,
    # so until then it isn't offered at sign-in or in Switch organisation (see Person.can_sign_in)
    verified_at = db.Column(db.DateTime(timezone=True))
    # set at registration, when the registrant ticks the terms and conditions. Who accepted is kept as their
    # name and email at the time rather than a foreign key to their person: it survives the person being
    # deleted, and person already refers to organisation, so a key back would be circular (see CLAUDE.md)
    terms_accepted_at = db.Column(db.DateTime(timezone=True))
    terms_accepted_by_name = db.Column(db.String(201))
    terms_accepted_by_email = db.Column(db.String(255))

    locations = db.relationship('Location', backref='organisation', lazy=True, cascade='delete')
    jobs = db.relationship('Job', backref='organisation', lazy=True, cascade='delete')
    assets = db.relationship('Asset', backref='organisation', lazy=True, cascade='delete')
    planned_job_schedules = db.relationship('PlannedJobSchedule', backref='organisation', lazy=True, cascade='delete')
    suppliers = db.relationship('Supplier', backref='organisation', lazy=True, cascade='delete')
    user_groups = db.relationship('UserGroup', backref='organisation', lazy=True, cascade='delete')
    people = db.relationship('Person', backref='organisation', lazy=True, cascade='delete')
    import_events = db.relationship('ImportEvent', backref='organisation', lazy=True, cascade='delete')
    documents = db.relationship('Document', backref='organisation', lazy=True, cascade='delete')
    document_exports = db.relationship('DocumentExport', backref='organisation', lazy=True, cascade='delete')
    job_categories = db.relationship('JobCategory', backref='organisation', lazy=True, cascade='delete')
    asset_categories = db.relationship('AssetCategory', backref='organisation', lazy=True, cascade='delete')
    asset_conditions = db.relationship('AssetCondition', backref='organisation', lazy=True, cascade='delete')
    location_categories = db.relationship('LocationCategory', backref='organisation', lazy=True, cascade='delete')
    supplier_categories = db.relationship('SupplierCategory', backref='organisation', lazy=True, cascade='delete')
    document_categories = db.relationship('DocumentCategory', backref='organisation', lazy=True, cascade='delete')
    support_tickets = db.relationship('SupportTicket', backref='organisation', lazy=True, cascade='delete')
    notifications = db.relationship('Notification', backref='organisation', lazy=True, cascade='delete')


### REFERENCE DATA ###
# each organisation's own lists of categories and conditions, managed under /reference-data
# (services/reference_data.py). Records point at them with ondelete SET NULL, so deleting one leaves
# its records with none

class ReferenceItem:
    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    description = db.Column(db.String(100), nullable=False)

    @declared_attr
    def organisation_id(cls):
        return db.Column(UUID(as_uuid=True), db.ForeignKey('organisation.id'), nullable=False)

    # unique per organisation. The routes and imports also check case-insensitively
    @declared_attr
    def __table_args__(cls):
        return (db.UniqueConstraint('organisation_id', 'description'),)

    # so templates can show {{ asset.asset_category or '-' }}
    def __str__(self):
        return self.description


class JobCategory(ReferenceItem, db.Model):
    pass


class AssetCategory(ReferenceItem, db.Model):
    pass


class AssetCondition(ReferenceItem, db.Model):
    pass


class SupplierCategory(ReferenceItem, db.Model):
    pass


# what kind of place a location is (Site, Building, Floor...), since locations nest freely
class LocationCategory(ReferenceItem, db.Model):
    pass


class DocumentCategory(ReferenceItem, db.Model):
    pass


# where things are: a site, a building in it, a floor or room in that... Locations nest to any depth through
# parent_id. Assets, jobs and planned job schedules are each at one location. materialised_path is the names from the
# top down, joined with ' > ' (services/locations.py keeps it up to date), and is unique per organisation,
# ignoring case
class Location(db.Model):
    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    # can't contain '>', the path separator
    name = db.Column(db.String(100), nullable=False)
    # one line per row, separated by newlines, see services/addresses.py
    address = db.Column(db.String(500))
    telephone = db.Column(db.String(100))
    # e.g. 'Head Office > Main Building > Ground Floor'. Set by the app (set_path), never a form
    materialised_path = db.Column(db.String(1000), nullable=False)

    organisation_id = db.Column(UUID(as_uuid=True), db.ForeignKey('organisation.id'), nullable=False)
    # set when the record came from a CSV import
    import_event_id = db.Column(UUID(as_uuid=True), db.ForeignKey('import_event.id', ondelete='SET NULL'))
    # the location this one is in, or None for a top-level location. Deleting a location deletes the
    # locations in it, and everything at them
    parent_id = db.Column(UUID(as_uuid=True), db.ForeignKey('location.id', ondelete='CASCADE'))
    parent = db.relationship('Location', remote_side='Location.id', backref=db.backref(
        'children', lazy=True, order_by='Location.name', cascade='delete'))
    # what kind of place it is. Chosen on the location form only, and filtered on the locations index
    location_category_id = db.Column(UUID(as_uuid=True), db.ForeignKey('location_category.id', ondelete='SET NULL'))
    location_category = db.relationship('LocationCategory', lazy=True)
    # someone in the organisation to contact about the location. Cleared if they're deleted
    main_contact_id = db.Column(UUID(as_uuid=True), db.ForeignKey('person.id', ondelete='SET NULL'))
    main_contact = db.relationship('Person', lazy=True, backref=db.backref(
        'main_contact_locations', lazy=True, order_by='Location.materialised_path'))

    jobs = db.relationship('Job', backref='location', lazy=True, cascade='delete')
    assets = db.relationship('Asset', backref='location', lazy=True, cascade='delete')
    planned_job_schedules = db.relationship('PlannedJobSchedule', backref='location', lazy=True, cascade='delete')

    # the routes and imports also check case-insensitively
    __table_args__ = (
        db.UniqueConstraint('organisation_id', 'materialised_path'),
    )

    def __str__(self):
        return self.materialised_path


class Job(db.Model):
    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    description = db.Column(db.String(100), nullable=False)
    work_required = db.Column(db.String(500), nullable=False)
    due_by = db.Column(db.Date())
    # not on JobForm: copied from the planned job schedule when a job is raised from one
    statutory_compliance = db.Column(db.Boolean, default=False, nullable=False)
    # not on JobForm: a key from JOB_STATUSES, set programmatically
    status = db.Column(db.String(20), default='new', server_default='new', nullable=False)
    # the UK date it was closed, set with status by the Close button (or Update schedule, for a planned job)
    closed_at = db.Column(db.Date())
    # the person who closed it. Reopening clears it, with closed_at
    closed_by_id = db.Column(UUID(as_uuid=True), db.ForeignKey('person.id', ondelete='SET NULL'))
    closed_by = db.relationship('Person', foreign_keys=[closed_by_id], backref=db.backref('closed_jobs', lazy=True), lazy=True)
    # set with status when a new job is rejected, on JobRejectForm only
    rejection_reason = db.Column(db.Text)
    # when a new job was approved or rejected. Reopening a rejected job clears rejected_at
    approved_at = db.Column(db.DateTime(timezone=True))
    rejected_at = db.Column(db.DateTime(timezone=True))
    # when it was last assigned or reassigned (Assign, Reassign)
    assigned_at = db.Column(db.DateTime(timezone=True))
    # set with status by Start (now) and Complete (entered in UK time on JobCompleteForm, with the comments)
    started_at = db.Column(db.DateTime(timezone=True))
    completed_at = db.Column(db.DateTime(timezone=True))
    completion_comments = db.Column(db.Text)
    # not on JobForm: a key from JOB_SOURCES. No default, so every route that makes a job must set it
    source = db.Column(db.String(20), nullable=False)

    organisation_id = db.Column(UUID(as_uuid=True), db.ForeignKey('organisation.id'), nullable=False)
    # set when the record came from a CSV import
    import_event_id = db.Column(UUID(as_uuid=True), db.ForeignKey('import_event.id', ondelete='SET NULL'))
    # deleting the location deletes the record
    location_id = db.Column(UUID(as_uuid=True), db.ForeignKey('location.id', ondelete='CASCADE'), nullable=False)
    planned_job_schedule_id = db.Column(UUID(as_uuid=True), db.ForeignKey('planned_job_schedule.id', ondelete='RESTRICT'))
    # copied from the planned job schedule when a job is raised from one
    job_category_id = db.Column(UUID(as_uuid=True), db.ForeignKey('job_category.id', ondelete='SET NULL'))
    job_category = db.relationship('JobCategory', lazy=True)
    # the job this is follow-up work for, set when it's created from that job's Follow Up Jobs tab, and never
    # changed. Deleting the parent leaves its follow-up jobs without one
    parent_job_id = db.Column(UUID(as_uuid=True), db.ForeignKey('job.id', ondelete='SET NULL'))
    parent_job = db.relationship('Job', remote_side='Job.id', foreign_keys=[parent_job_id],
                                 backref=db.backref('follow_up_jobs', lazy=True, order_by='Job.created_at'))
    # pounds, shown with the currency filter. estimated_cost is copied from the planned job schedule by raise_job()
    estimated_cost = db.Column(db.Numeric(12, 2))
    actual_cost = db.Column(db.Numeric(12, 2))

    # who the job is assigned to: one supplier or one person, never both (ck_job_assignee). They're kept
    # for audit, so a supplier or person with jobs assigned can't be deleted (the routes refuse, and the
    # FKs are RESTRICT). passive_deletes='all': the ORM never clears them, so the database refuses instead
    assigned_supplier_id = db.Column(UUID(as_uuid=True), db.ForeignKey('supplier.id', ondelete='RESTRICT'))
    assigned_person_id = db.Column(UUID(as_uuid=True), db.ForeignKey('person.id', ondelete='RESTRICT'))
    assigned_supplier = db.relationship('Supplier', foreign_keys=[assigned_supplier_id], lazy=True, backref=db.backref(
        'assigned_jobs', lazy=True, passive_deletes='all'))
    assigned_person = db.relationship('Person', foreign_keys=[assigned_person_id], lazy=True, backref=db.backref(
        'assigned_jobs', lazy=True, passive_deletes='all'))
    # people helping the assignee. They aren't notified, and can't start or complete the job
    helpers = db.relationship('Person', secondary=job_helpers, lazy=True,
                              order_by=lambda: (Person.first_name, Person.last_name),
                              backref=db.backref('helping_jobs', lazy=True))

    __table_args__ = (
        db.CheckConstraint(
            "status IN ({})".format(', '.join(f"'{key}'" for key, _ in JOB_STATUSES)),
            name='ck_job_status',
        ),
        db.CheckConstraint(
            "source IN ({})".format(', '.join(f"'{key}'" for key, _ in JOB_SOURCES)),
            name='ck_job_source',
        ),
        db.CheckConstraint(
            'assigned_supplier_id IS NULL OR assigned_person_id IS NULL',
            name='ck_job_assignee',
        ),
        # backstop for the sequence rules: a planned job schedule has at most one job that isn't closed or rejected
        db.Index(
            'uq_job_open_per_schedule', 'planned_job_schedule_id', unique=True,
            postgresql_where=db.text("status NOT IN ('closed', 'rejected')"),
            sqlite_where=db.text("status NOT IN ('closed', 'rejected')"),
        ),
    )

    @property
    def status_label(self):
        return JOB_STATUS_LABELS.get(self.status, '-')

    @property
    def source_label(self):
        return JOB_SOURCE_LABELS.get(self.source, '-')

    # the UK date the job was created. It can't be closed before this
    @property
    def raised_on(self):
        return uk_date(self.created_at)

    # the UK date the job was completed (completed_at). A planned job's next due date is worked out from it
    @property
    def completed_on(self):
        return uk_date(self.completed_at) if self.completed_at else None

    # a planned job whose schedule is waiting on it (raise_job cleared the creation date). Closing it goes
    # through jobs.update_schedule, and rejecting it asks for the next due date too, so the job and the
    # schedule's next due date are saved together
    @property
    def closes_with_schedule_update(self):
        return (
            self.source == 'planned'
            and self.planned_job_schedule is not None
            and self.planned_job_schedule.next_job_creation_date is None
        )

    # the supplier or person it's assigned to, or None
    @property
    def assignee(self):
        return self.assigned_supplier or self.assigned_person

    @property
    def assignee_name(self):
        if self.assigned_supplier:
            return self.assigned_supplier.name
        if self.assigned_person:
            return f'{self.assigned_person.first_name} {self.assigned_person.last_name}'
        return None

    # the signed-in person is the job's assignee, so they can open it, start it and complete it
    def is_assignee(self, person):
        return person is not None and self.assigned_person_id is not None and self.assigned_person_id == person.id


class Notification(db.Model):
    """An in-app message to a person who can sign in, shown under the navbar's bell (services/notifications.py).
    People who can't sign in, and suppliers, are emailed instead. It's deleted with its person, and with its
    job (both CASCADE in the database)."""
    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    message = db.Column(db.String(255), nullable=False)
    # set when they open it (its View button) or mark them all read
    read_at = db.Column(db.DateTime(timezone=True))

    organisation_id = db.Column(UUID(as_uuid=True), db.ForeignKey('organisation.id'), nullable=False)
    person_id = db.Column(UUID(as_uuid=True), db.ForeignKey('person.id', ondelete='CASCADE'), nullable=False)
    # what View opens. The only kind so far is a job assigned to them
    job_id = db.Column(UUID(as_uuid=True), db.ForeignKey('job.id', ondelete='CASCADE'))
    # one-way relationships, so the ORM never tries to clear these when a person or job is deleted
    person = db.relationship('Person', lazy=True)
    job = db.relationship('Job', lazy=True)


class Asset(db.Model):
    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    description = db.Column(db.String(50), nullable=False)
    asset_number = db.Column(db.String(50))
    make = db.Column(db.String(50))
    model = db.Column(db.String(50))
    serial_number = db.Column(db.String(50))
    warranty_expiry = db.Column(db.Date())
    purchase_date = db.Column(db.Date())
    purchased_from = db.Column(db.String(100))
    estimated_replacement_date = db.Column(db.Date())
    # pounds, shown with the currency filter
    estimated_replacement_cost = db.Column(db.Numeric(12, 2))

    organisation_id = db.Column(UUID(as_uuid=True), db.ForeignKey('organisation.id'), nullable=False)
    # set when the record came from a CSV import
    import_event_id = db.Column(UUID(as_uuid=True), db.ForeignKey('import_event.id', ondelete='SET NULL'))
    # deleting the location deletes the record
    location_id = db.Column(UUID(as_uuid=True), db.ForeignKey('location.id', ondelete='CASCADE'), nullable=False)
    # the person in this organisation the asset is assigned to, set on the Assign page. Cleared if they're deleted
    assigned_to_id = db.Column(UUID(as_uuid=True), db.ForeignKey('person.id', ondelete='SET NULL'))
    assigned_to = db.relationship('Person', backref=db.backref('assigned_assets', lazy=True, order_by='Asset.description'))
    # the asset this is part of. Set only when a sub-asset is created from its parent's Sub-Assets tab, and
    # never changed, so there can't be a loop. Deleting the parent leaves its sub-assets as top-level assets
    parent_id = db.Column(UUID(as_uuid=True), db.ForeignKey('asset.id', ondelete='SET NULL'))
    parent = db.relationship('Asset', remote_side='Asset.id', backref=db.backref('sub_assets', lazy=True, order_by='Asset.description'))
    asset_category_id = db.Column(UUID(as_uuid=True), db.ForeignKey('asset_category.id', ondelete='SET NULL'))
    asset_category = db.relationship('AssetCategory', lazy=True)
    asset_condition_id = db.Column(UUID(as_uuid=True), db.ForeignKey('asset_condition.id', ondelete='SET NULL'))
    asset_condition = db.relationship('AssetCondition', lazy=True)
    # the UK date the condition was last changed, set by the app (or by an import)
    condition_set = db.Column(db.Date())

    jobs = db.relationship('Job', secondary=job_assets, backref='assets', lazy=True)
    planned_job_schedules = db.relationship('PlannedJobSchedule', secondary=planned_job_schedule_assets, backref='assets', lazy=True)

    # asset_number and serial_number are optional and unique per organisation. Blanks are stored
    # as NULL so the constraints allow many assets without one. The routes also check case-insensitively
    __table_args__ = (
        db.UniqueConstraint('organisation_id', 'asset_number'),
        db.UniqueConstraint('organisation_id', 'serial_number'),
    )


class PlannedJobSchedule(db.Model):
    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    description = db.Column(db.String(100), nullable=False)
    work_required = db.Column(db.String(500), nullable=False)
    next_due_by = db.Column(db.Date(), nullable=False)
    statutory_compliance = db.Column(db.Boolean, default=False, nullable=False)
    # an inactive schedule raises no jobs. Making it active again needs a next due date in the future
    is_active = db.Column(db.Boolean, default=True, server_default=db.text('true'), nullable=False)
    # a key from services/scheduling.py FREQUENCIES
    frequency = db.Column(db.String(20), nullable=False)
    # how many days ahead of next_due_by the job is created
    notice_days = db.Column(db.Integer, nullable=False)
    # calculated from next_due_by and notice_days, never taken from a form. NULL once a job has been
    # raised for next_due_by, so the schedule raises nothing more while that job is waiting
    next_job_creation_date = db.Column(db.Date())

    organisation_id = db.Column(UUID(as_uuid=True), db.ForeignKey('organisation.id'), nullable=False)
    # set when the record came from a CSV import
    import_event_id = db.Column(UUID(as_uuid=True), db.ForeignKey('import_event.id', ondelete='SET NULL'))
    # deleting the location deletes the record
    location_id = db.Column(UUID(as_uuid=True), db.ForeignKey('location.id', ondelete='CASCADE'), nullable=False)
    # raise_job() copies it to the job
    job_category_id = db.Column(UUID(as_uuid=True), db.ForeignKey('job_category.id', ondelete='SET NULL'))
    job_category = db.relationship('JobCategory', lazy=True)
    # pounds, shown with the currency filter. raise_job() copies it to the job
    estimated_cost = db.Column(db.Numeric(12, 2))
    # in days, so half a day is 0.5. Shown with the days filter
    estimated_duration = db.Column(db.Numeric(6, 2))

    jobs = db.relationship('Job', backref='planned_job_schedule', lazy=True)

    @property
    def frequency_label(self):
        return FREQUENCY_LABELS.get(self.frequency, '-')

    # Next Job Creation as shown on pages and downloads
    @property
    def next_job_creation_label(self):
        if not self.is_active:
            return 'Inactive'
        if self.next_job_creation_date is None:
            return 'Pending current job closure'
        return self.next_job_creation_date.strftime('%d-%m-%Y')

    # a job raised from the schedule that isn't closed or rejected yet, or None. The schedule can't be edited
    # while there is one, so it can never have two open jobs
    @property
    def open_job(self):
        return Job.query.filter(Job.planned_job_schedule_id == self.id, Job.status.notin_(JOB_FINISHED_STATUSES)).order_by(Job.due_by, Job.id).first()


class Supplier(db.Model):
    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    name = db.Column(db.String(100), nullable=False)
    # one line per row, separated by newlines, see services/addresses.py
    address = db.Column(db.String(500))
    telephone = db.Column(db.String(100))
    # optional contact email. Blank is stored as NULL
    email = db.Column(db.String(255))

    organisation_id = db.Column(UUID(as_uuid=True), db.ForeignKey('organisation.id'), nullable=False)
    # set when the record came from a CSV import
    import_event_id = db.Column(UUID(as_uuid=True), db.ForeignKey('import_event.id', ondelete='SET NULL'))
    supplier_category_id = db.Column(UUID(as_uuid=True), db.ForeignKey('supplier_category.id', ondelete='SET NULL'))
    supplier_category = db.relationship('SupplierCategory', lazy=True)


# the login: one per email, across every organisation. What the user can do in an organisation
# (can_login, user group) is on their Person there. A platform admin may have no people at all
class User(db.Model):
    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    email = db.Column(db.String(255), nullable=False, unique=True)
    email_verified_at = db.Column(db.DateTime(timezone=True))
    # MyFmHub staff, can see /admin. Only set with the `flask make-platform-admin` command
    is_platform_admin = db.Column(db.Boolean, default=False, nullable=False)
    # the organisation they last worked in, chosen first at their next sign-in
    last_organisation_id = db.Column(UUID(as_uuid=True), db.ForeignKey('organisation.id', ondelete='SET NULL'))

    otps = db.relationship('OTP', backref='user', lazy=True, cascade='delete')
    sessions = db.relationship('UserSession', backref='user', lazy=True, cascade='delete')

    def signin_people(self, otp=None):
        """The user's people they can sign in as, by organisation name. See Person.can_sign_in."""
        return sorted(
            [person for person in self.people if person.can_sign_in(otp)],
            key=lambda person: (person.organisation.name.lower(), person.organisation_id),
        )


# someone in an organisation, and their membership of it. Every person has a user, the login, which is
# shared by their people in other organisations. can_login and the user group belong to this organisation
class Person(db.Model):
    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    first_name = db.Column(db.String(100), nullable=False)
    last_name = db.Column(db.String(100), nullable=False)
    # always the same as user.email. Changing it moves the person to the user with the new email
    email = db.Column(db.String(255), nullable=False)
    job_title = db.Column(db.String(100))
    mobile = db.Column(db.String(100))
    # set by the organisation. People without a login are still in the app
    can_login = db.Column(db.Boolean, default=False, nullable=False)

    organisation_id = db.Column(UUID(as_uuid=True), db.ForeignKey('organisation.id'), nullable=False)
    # set when the record came from a CSV import
    import_event_id = db.Column(UUID(as_uuid=True), db.ForeignKey('import_event.id', ondelete='SET NULL'))
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    # only required when can_login is on (enforced by PersonForm). People who can't log in may have no group
    user_group_id = db.Column(UUID(as_uuid=True), db.ForeignKey('user_group.id', ondelete='RESTRICT'))

    user = db.relationship('User', backref=db.backref('people', lazy=True), lazy=True)

    # a user is in an organisation at most once
    __table_args__ = (
        db.UniqueConstraint('organisation_id', 'user_id'),
    )

    @property
    def full_name(self):
        return f'{self.first_name} {self.last_name}'

    @property
    def is_org_admin(self):
        return self.user_group is not None and self.user_group.is_admin

    def can_sign_in(self, otp=None):
        """Login on and the organisation not suspended. The organisation must also be verified, unless otp is
        the code sent when this person registered it (entering that code is what verifies it)."""
        organisation = self.organisation
        return (
            self.can_login
            and not organisation.suspended_at
            and (organisation.verified_at is not None or (otp is not None and otp.person_id == self.id))
        )


class OTP(db.Model):
    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    # HMAC-SHA256 of the code with OTP_PEPPER, the code itself is never stored
    code_hash = db.Column(db.String(64), nullable=False)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    used_at = db.Column(db.DateTime(timezone=True))
    attempts = db.Column(db.Integer, default=0, nullable=False)

    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    # the organisation the code was sent for (registration or an invite): signing in with it goes there
    person_id = db.Column(UUID(as_uuid=True), db.ForeignKey('person.id', ondelete='SET NULL'))

    person = db.relationship('Person', lazy=True)


class UserSession(db.Model):
    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    # SHA-256 of the token in the auth cookie, the token itself is never stored
    token_hash = db.Column(db.String(64), nullable=False, unique=True)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    revoked_at = db.Column(db.DateTime(timezone=True))
    last_seen_at = db.Column(db.DateTime(timezone=True))
    user_agent = db.Column(db.String(255))
    ip_address = db.Column(db.String(45))

    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    # the organisation the session is working in, as the user's person there. NULL while a user with
    # several organisations chooses one, or for a platform admin outside any organisation
    person_id = db.Column(UUID(as_uuid=True), db.ForeignKey('person.id', ondelete='SET NULL'))

    person = db.relationship('Person', lazy=True)


class UserGroup(db.Model):
    """A permission group within an organisation. Every person who can log in is in exactly one."""
    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    name = db.Column(db.String(100), nullable=False)
    # the organisation's Admin group: full access, and it can't be renamed, changed or deleted
    is_admin = db.Column(db.Boolean, default=False, nullable=False)

    organisation_id = db.Column(UUID(as_uuid=True), db.ForeignKey('organisation.id'), nullable=False)

    people = db.relationship('Person', backref='user_group', lazy=True)
    permissions = db.relationship('UserGroupPermission', lazy=True, cascade='all, delete-orphan')

    __table_args__ = (db.UniqueConstraint('organisation_id', 'name'),)

    @property
    def permission_keys(self):
        return {p.permission for p in self.permissions}


class UserGroupPermission(db.Model):
    """One permission (for example 'jobs.delete') granted to a group. Like the link tables, it has no id or timestamps."""
    user_group_id = db.Column(UUID(as_uuid=True), db.ForeignKey('user_group.id', ondelete='CASCADE'), primary_key=True)
    permission = db.Column(db.String(50), primary_key=True)


class ImportEvent(db.Model):
    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    # a key from services/imports.py IMPORT_TYPES
    import_type = db.Column(db.String(30), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    rows_imported = db.Column(db.Integer, default=0, nullable=False)
    # the rows that weren't imported, as [{'row': 3, 'error': '...'}]. Row numbers count the header as row 1
    errors = db.Column(db.JSON, default=list, nullable=False)

    organisation_id = db.Column(UUID(as_uuid=True), db.ForeignKey('organisation.id'), nullable=False)
    # the names of the people who imported it and undid it, at the time. Names rather than foreign keys to
    # person: they survive the person being deleted, and person has import_event_id, so keys back would be
    # circular (see CLAUDE.md)
    imported_by_name = db.Column(db.String(201))
    # set by undo, which deletes the imported records but keeps this as history
    undone_at = db.Column(db.DateTime)
    undone_by_name = db.Column(db.String(201))


class Document(db.Model):
    """An uploaded document, stored in DigitalOcean Spaces under the organisation's id (services/documents.py).
    Only the description, category and expiry date can be edited. The stored file, its name, type and
    size never change: they're uploaded, linked and unlinked, and deleted."""
    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    description = db.Column(db.String(255), nullable=False)
    expires = db.Column(db.Date())
    # in MB, summed for the organisation's Storage Used on My Organisation
    size_mb = db.Column(db.Numeric(12, 4), nullable=False)
    # the uploaded file's name (for downloads), its type (from the extension, not the browser) and
    # where it's stored: '<organisation id>/<document id>.<extension>'
    filename = db.Column(db.String(255), nullable=False)
    content_type = db.Column(db.String(100), nullable=False)
    storage_key = db.Column(db.String(255), nullable=False, unique=True)

    organisation_id = db.Column(UUID(as_uuid=True), db.ForeignKey('organisation.id'), nullable=False)
    # chosen at upload, like everything else on a document
    document_category_id = db.Column(UUID(as_uuid=True), db.ForeignKey('document_category.id', ondelete='SET NULL'))
    document_category = db.relationship('DocumentCategory', lazy=True)

    # the records it's linked to. Each also has .documents
    locations = db.relationship('Location', secondary=location_documents, backref=db.backref('documents', order_by='Document.description'), lazy=True)
    jobs = db.relationship('Job', secondary=job_documents, backref=db.backref('documents', order_by='Document.description'), lazy=True)
    assets = db.relationship('Asset', secondary=asset_documents, backref=db.backref('documents', order_by='Document.description'), lazy=True)
    # named after its DOCUMENT_PARENTS key, which is the resource name rather than the model's
    planned_jobs = db.relationship('PlannedJobSchedule', secondary=planned_job_schedule_documents, backref=db.backref('documents', order_by='Document.description'), lazy=True)
    people = db.relationship('Person', secondary=person_documents, backref=db.backref('documents', order_by='Document.description'), lazy=True)
    suppliers = db.relationship('Supplier', secondary=supplier_documents, backref=db.backref('documents', order_by='Document.description'), lazy=True)

    @property
    def is_expired(self):
        return self.expires is not None and self.expires < uk_today()


DOCUMENT_EXPORT_STATUSES = [('running', 'Preparing'), ('ready', 'Ready'), ('failed', 'Failed'), ('expired', 'Expired')]


class DocumentExport(db.Model):
    """A request to zip all the organisation's documents, from Export data. A thread started by the request
    (services/document_exports.py) builds the zip, stores it and emails the requester."""
    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    # a key from DOCUMENT_EXPORT_STATUSES. running -> ready (or failed); ready -> expired once the zip is deleted
    status = db.Column(db.String(20), default='running', nullable=False)
    # where to send the email, kept in case the person is deleted while it runs
    email = db.Column(db.String(255), nullable=False)
    started_at = db.Column(db.DateTime(timezone=True))
    finished_at = db.Column(db.DateTime(timezone=True))
    # when the zip is deleted from storage
    expires_at = db.Column(db.DateTime(timezone=True))
    storage_key = db.Column(db.String(255))
    document_count = db.Column(db.Integer)
    size_mb = db.Column(db.Numeric(12, 4))
    error = db.Column(db.String(255))

    organisation_id = db.Column(UUID(as_uuid=True), db.ForeignKey('organisation.id'), nullable=False)
    person_id = db.Column(UUID(as_uuid=True), db.ForeignKey('person.id', ondelete='SET NULL'))
    person = db.relationship('Person', lazy=True)

    __table_args__ = (
        db.CheckConstraint(f"status IN ({', '.join(repr(key) for key, _ in DOCUMENT_EXPORT_STATUSES)})", name='ck_document_export_status'),
    )

    @property
    def status_label(self):
        return dict(DOCUMENT_EXPORT_STATUSES)[self.status]


# a support ticket's state. Platform admins close and reopen it in /admin; its creator only sees this
SUPPORT_TICKET_STATUSES = [('open', 'Open'), ('closed', 'Closed')]
SUPPORT_TICKET_STATUS_LABELS = dict(SUPPORT_TICKET_STATUSES)


class SupportTicket(db.Model):
    """An issue or question raised from the Support page by someone in an organisation. Platform admins
    handle it in /admin/support-tickets, with notes its creator never sees."""
    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    description = db.Column(db.Text, nullable=False)
    # a key from SUPPORT_TICKET_STATUSES, set by the app, never a form
    status = db.Column(db.String(20), default='open', server_default='open', nullable=False)

    organisation_id = db.Column(UUID(as_uuid=True), db.ForeignKey('organisation.id'), nullable=False)
    # the person who raised it. Kept (as NULL) if they're deleted, so the ticket and its notes stay
    created_by_id = db.Column(UUID(as_uuid=True), db.ForeignKey('person.id', ondelete='SET NULL'))
    created_by = db.relationship('Person', backref=db.backref('support_tickets', lazy=True), lazy=True)

    # platform admins' notes, oldest first. Never shown to the ticket's creator
    notes = db.relationship('SupportTicketNote', backref='support_ticket', lazy=True, cascade='all, delete-orphan',
                            order_by='SupportTicketNote.created_at')

    __table_args__ = (
        db.CheckConstraint(f"status IN ({', '.join(repr(key) for key, _ in SUPPORT_TICKET_STATUSES)})", name='ck_support_ticket_status'),
    )

    @property
    def status_label(self):
        return SUPPORT_TICKET_STATUS_LABELS.get(self.status, '-')


class SupportTicketNote(db.Model):
    """A platform admin's note on a support ticket. It belongs to the ticket (deleted with it), so like
    UserGroupPermission it has no organisation_id of its own."""
    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    description = db.Column(db.Text, nullable=False)

    support_ticket_id = db.Column(UUID(as_uuid=True), db.ForeignKey('support_ticket.id', ondelete='CASCADE'), nullable=False)
    # the platform admin (a login, who may be in no organisation). Kept (as NULL) if the user is deleted
    created_by_id = db.Column(UUID(as_uuid=True), db.ForeignKey('user.id', ondelete='SET NULL'))
    created_by = db.relationship('User', backref=db.backref('support_ticket_notes', lazy=True), lazy=True)
