from app.extensions import db
import uuid
from sqlalchemy.dialects.postgresql import UUID
from datetime import datetime, timezone
from app.services.scheduling import FREQUENCY_LABELS, uk_date

# a job's lifecycle. The key is stored on Job.status, which is set by the app, never a form
JOB_STATUSES = [
    ('new', 'New'),
    ('open', 'Open'),
    ('closed', 'Closed'),
]
JOB_STATUS_LABELS = dict(JOB_STATUSES)

# where a job came from. The key is stored on Job.source, which is set by the app, never a form
JOB_SOURCES = [
    ('planned', 'Planned'),
    ('reactive', 'Reactive'),
    ('remedial', 'Remedial'),
]
JOB_SOURCE_LABELS = dict(JOB_SOURCES)

job_assets = db.Table(
    'job_assets',
    db.Column('job_id', UUID(as_uuid=True), db.ForeignKey('job.id', ondelete='CASCADE'), primary_key=True),
    db.Column('asset_id', UUID(as_uuid=True), db.ForeignKey('asset.id', ondelete='CASCADE'), primary_key=True),
)

job_schedule_assets = db.Table(
    'job_schedule_assets',
    db.Column('job_schedule_id', UUID(as_uuid=True), db.ForeignKey('job_schedule.id', ondelete='CASCADE'), primary_key=True),
    db.Column('asset_id', UUID(as_uuid=True), db.ForeignKey('asset.id', ondelete='CASCADE'), primary_key=True),
)

class Organisation(db.Model):
    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    name = db.Column(db.String(100), nullable=False)
    # set by a platform admin. Nobody can sign in to a suspended organisation
    suspended_at = db.Column(db.DateTime(timezone=True))
    # set when the person who registered it first enters a code. Anyone can register with any email,
    # so until then it isn't offered at sign-in or in Switch organisation (see Person.can_sign_in)
    verified_at = db.Column(db.DateTime(timezone=True))

    sites = db.relationship('Site', backref='organisation', lazy=True, cascade='delete')
    jobs = db.relationship('Job', backref='organisation', lazy=True, cascade='delete')
    assets = db.relationship('Asset', backref='organisation', lazy=True, cascade='delete')
    job_schedules = db.relationship('JobSchedule', backref='organisation', lazy=True, cascade='delete')
    suppliers = db.relationship('Supplier', backref='organisation', lazy=True, cascade='delete')
    user_groups = db.relationship('UserGroup', backref='organisation', lazy=True, cascade='delete')
    people = db.relationship('Person', backref='organisation', lazy=True, cascade='delete')
    import_events = db.relationship('ImportEvent', backref='organisation', lazy=True, cascade='delete')
    buildings = db.relationship('Building', backref='organisation', lazy=True, cascade='delete')
    zones = db.relationship('Zone', backref='organisation', lazy=True, cascade='delete')


class Site(db.Model):
    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    name = db.Column(db.String(100), nullable=False)
    address_line_one = db.Column(db.String(100))
    address_line_two = db.Column(db.String(100))
    town_city = db.Column(db.String(100))
    post_code = db.Column(db.String(100))
    telephone = db.Column(db.String(100))

    organisation_id = db.Column(UUID(as_uuid=True), db.ForeignKey('organisation.id'), nullable=False)
    # set when the record came from a CSV import
    import_event_id = db.Column(UUID(as_uuid=True), db.ForeignKey('import_event.id', ondelete='SET NULL'))

    jobs = db.relationship('Job', backref='site', lazy=True, cascade='delete')
    assets = db.relationship('Asset', backref='site', lazy=True, cascade='delete')
    job_schedules = db.relationship('JobSchedule', backref='site', lazy=True, cascade='delete')
    buildings = db.relationship('Building', backref='site', lazy=True, cascade='delete', order_by='Building.name')

    # unique per organisation. The routes and imports also check case-insensitively
    __table_args__ = (
        db.UniqueConstraint('organisation_id', 'name'),
    )


# a building at a site. Assets, jobs and job schedules can optionally say which building (and zone) they're in
class Building(db.Model):
    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    name = db.Column(db.String(100), nullable=False)

    organisation_id = db.Column(UUID(as_uuid=True), db.ForeignKey('organisation.id'), nullable=False)
    # set when the record came from a CSV import
    import_event_id = db.Column(UUID(as_uuid=True), db.ForeignKey('import_event.id', ondelete='SET NULL'))
    # never changes after creation, so the records in it always stay in its site
    site_id = db.Column(UUID(as_uuid=True), db.ForeignKey('site.id'), nullable=False)

    zones = db.relationship('Zone', backref='building', lazy=True, cascade='delete', order_by='Zone.name')
    # deleting the building clears building_id (and zone_id, through the zones) on these
    assets = db.relationship('Asset', backref='building', lazy=True)
    jobs = db.relationship('Job', backref='building', lazy=True)
    job_schedules = db.relationship('JobSchedule', backref='building', lazy=True)

    # unique within the site. The routes and imports also check case-insensitively
    __table_args__ = (
        db.UniqueConstraint('site_id', 'name'),
    )


# an area of a building, such as a floor or plant room
class Zone(db.Model):
    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    name = db.Column(db.String(100), nullable=False)

    organisation_id = db.Column(UUID(as_uuid=True), db.ForeignKey('organisation.id'), nullable=False)
    # set when the record came from a CSV import
    import_event_id = db.Column(UUID(as_uuid=True), db.ForeignKey('import_event.id', ondelete='SET NULL'))
    # never changes after creation, like Building.site_id
    building_id = db.Column(UUID(as_uuid=True), db.ForeignKey('building.id', ondelete='CASCADE'), nullable=False)

    assets = db.relationship('Asset', backref='zone', lazy=True)
    jobs = db.relationship('Job', backref='zone', lazy=True)
    job_schedules = db.relationship('JobSchedule', backref='zone', lazy=True)

    # unique within the building. The routes and imports also check case-insensitively
    __table_args__ = (
        db.UniqueConstraint('building_id', 'name'),
    )


class Job(db.Model):
    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    description = db.Column(db.String(100), nullable=False)
    work_required = db.Column(db.String(500), nullable=False)
    due_by = db.Column(db.Date())
    # not on JobForm: copied from the job schedule when a job is raised from one
    statutory_compliance = db.Column(db.Boolean, default=False, nullable=False)
    # not on JobForm: a key from JOB_STATUSES, set programmatically
    status = db.Column(db.String(20), default='new', server_default='new', nullable=False)
    # set with status when the job is closed, on JobCloseForm only
    closed_at = db.Column(db.Date())
    # not on JobForm: a key from JOB_SOURCES. No default, so every route that makes a job must set it
    source = db.Column(db.String(20), nullable=False)

    organisation_id = db.Column(UUID(as_uuid=True), db.ForeignKey('organisation.id'), nullable=False)
    site_id = db.Column(UUID(as_uuid=True), db.ForeignKey('site.id'), nullable=False)
    # optional, and always in the record's site (zone in the building). Cleared if the building or zone is deleted
    building_id = db.Column(UUID(as_uuid=True), db.ForeignKey('building.id', ondelete='SET NULL'))
    zone_id = db.Column(UUID(as_uuid=True), db.ForeignKey('zone.id', ondelete='SET NULL'))
    job_schedule_id = db.Column(UUID(as_uuid=True), db.ForeignKey('job_schedule.id', ondelete='RESTRICT'))

    __table_args__ = (
        db.CheckConstraint(
            "status IN ({})".format(', '.join(f"'{key}'" for key, _ in JOB_STATUSES)),
            name='ck_job_status',
        ),
        db.CheckConstraint(
            "source IN ({})".format(', '.join(f"'{key}'" for key, _ in JOB_SOURCES)),
            name='ck_job_source',
        ),
        # backstop for the sequence rules: a job schedule has at most one job that isn't closed
        db.Index(
            'uq_job_open_per_schedule', 'job_schedule_id', unique=True,
            postgresql_where=db.text("status != 'closed'"),
            sqlite_where=db.text("status != 'closed'"),
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

    # a planned job whose schedule is waiting on it (raise_job cleared the creation date). Closing it
    # goes through jobs.update_schedule, which closes the job and sets the schedule's next due date together
    @property
    def closes_with_schedule_update(self):
        return (
            self.source == 'planned'
            and self.job_schedule is not None
            and self.job_schedule.next_job_creation_date is None
        )


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
    site_id = db.Column(UUID(as_uuid=True), db.ForeignKey('site.id'), nullable=False)
    # optional, and always in the record's site (zone in the building). Cleared if the building or zone is deleted
    building_id = db.Column(UUID(as_uuid=True), db.ForeignKey('building.id', ondelete='SET NULL'))
    zone_id = db.Column(UUID(as_uuid=True), db.ForeignKey('zone.id', ondelete='SET NULL'))

    jobs = db.relationship('Job', secondary=job_assets, backref='assets', lazy=True)
    job_schedules = db.relationship('JobSchedule', secondary=job_schedule_assets, backref='assets', lazy=True)

    # asset_number and serial_number are optional and unique per organisation. Blanks are stored
    # as NULL so the constraints allow many assets without one. The routes also check case-insensitively
    __table_args__ = (
        db.UniqueConstraint('organisation_id', 'asset_number'),
        db.UniqueConstraint('organisation_id', 'serial_number'),
    )


class JobSchedule(db.Model):
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
    site_id = db.Column(UUID(as_uuid=True), db.ForeignKey('site.id'), nullable=False)
    # optional, and always in the record's site (zone in the building). Cleared if the building or zone is deleted
    building_id = db.Column(UUID(as_uuid=True), db.ForeignKey('building.id', ondelete='SET NULL'))
    zone_id = db.Column(UUID(as_uuid=True), db.ForeignKey('zone.id', ondelete='SET NULL'))

    jobs = db.relationship('Job', backref='job_schedule', lazy=True)

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

    # a job raised from the schedule that isn't closed yet, or None. The schedule can't be edited
    # while there is one, so it can never have two open jobs
    @property
    def open_job(self):
        return Job.query.filter(Job.job_schedule_id == self.id, Job.status != 'closed').order_by(Job.due_by, Job.id).first()


class Supplier(db.Model):
    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    name = db.Column(db.String(100), nullable=False)
    address_line_one = db.Column(db.String(100))
    address_line_two = db.Column(db.String(100))
    town_city = db.Column(db.String(100))
    post_code = db.Column(db.String(100))
    telephone = db.Column(db.String(100))

    organisation_id = db.Column(UUID(as_uuid=True), db.ForeignKey('organisation.id'), nullable=False)
    # set when the record came from a CSV import
    import_event_id = db.Column(UUID(as_uuid=True), db.ForeignKey('import_event.id', ondelete='SET NULL'))


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
    # the people in this organisation who imported it and undid it. Person has import_event_id too, so
    # these foreign keys are added after both tables are created (use_alter)
    person_id = db.Column(UUID(as_uuid=True), db.ForeignKey('person.id', ondelete='SET NULL', use_alter=True, name='fk_import_event_person_id'))
    # set by undo, which deletes the imported records but keeps this as history
    undone_at = db.Column(db.DateTime)
    undone_by_id = db.Column(UUID(as_uuid=True), db.ForeignKey('person.id', ondelete='SET NULL', use_alter=True, name='fk_import_event_undone_by_id'))

    person = db.relationship('Person', foreign_keys=[person_id], lazy=True)
    undone_by = db.relationship('Person', foreign_keys=[undone_by_id], lazy=True)
