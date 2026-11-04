import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from email_validator import EmailNotValidError, validate_email
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import ASSIGNED_JOB_STATUSES, JOB_STATUSES, Asset, ImportEvent, Job, PlannedJobSchedule, Location, Person, Supplier, User, UserGroup, JobCategory, AssetCategory, AssetCondition, LocationCategory, SupplierCategory
from app.services.addresses import MAX_ADDRESS_LENGTH, clean_address
from app.services.locations import MAX_PATH_LENGTH, build_path, normalise_path
from app.services.people import delete_person
from app.services.permissions import can
from app.services.scheduling import FREQUENCIES, FREQUENCY_LABELS, FREQUENCY_MIN_DAYS, job_creation_date, uk_today
from app.services.spreadsheets import SpreadsheetError, read_xlsx, template_xlsx

# Excel (.xlsx) imports. Each import type lists its columns; the header row must use the column keys.
# A file is refused outright if its headers are wrong. Otherwise every row is checked and saved
# on its own, so good rows are imported and bad ones are reported with the reason

MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 5000
DATE_FORMATS = ('%d/%m/%Y', '%d-%m-%Y', '%Y-%m-%d')
# a person written as "Sam Jones (sam@example.com)", as the Excel template's dropdowns show them. The email
# picks the person when two people share a name
PERSON_WITH_EMAIL = re.compile(r'^(.*?)\s*\(\s*([^()\s]+@[^()\s]+)\s*\)$')


class RowError(Exception):
    pass


@dataclass
class Column:
    key: str
    kind: str  # text, address, date, money, whole_number, days, yes_no, frequency, location, email, user_group, reference, job_status, assignee, helpers, person
    required: bool = False
    max_length: int = None
    notes: str = ''
    # yes_no only: what a blank cell means
    blank_means_yes: bool = False
    # reference only: the category or condition model (services/reference_data.py) the value must match
    reference: object = None

    @property
    def label(self):
        return self.key.replace('_', ' ').capitalize()

    @property
    def format(self):
        """How the value should be written, for the guidance page."""
        if self.kind == 'text':
            return f'Text, up to {self.max_length} characters'
        if self.kind == 'address':
            return f'Text, up to {self.max_length} characters. Separate the lines with commas, e.g. 1 High Street, Leeds, LS1 1AA'
        if self.kind == 'reference':
            return f'The description of one of your {self.label.lower().replace("category", "categories").replace("condition", "conditions")} (Reference data)'
        return {
            'date': 'Date: dd/mm/yyyy, dd-mm-yyyy or yyyy-mm-dd',
            'money': 'Amount in pounds, e.g. 1250.00 (£ and commas are allowed)',
            'whole_number': 'Whole number',
            'days': 'Number of days, e.g. 0.5 for half a day',
            'yes_no': f"Yes or No. Blank means {'Yes' if self.blank_means_yes else 'No'}",
            'frequency': 'One of: ' + ', '.join(label for _, label in FREQUENCIES),
            'location': "A location's full path, e.g. Head Office > Main Building",
            'email': f'Email address, up to {self.max_length} characters',
            'user_group': 'The name of an existing user group',
            'job_status': 'One of: ' + ', '.join(label for _, label in JOB_STATUSES) + '. Blank means New',
            'assignee': 'The name of an existing supplier, or the full name of someone in People, '
                        'optionally with their email in brackets, e.g. Sam Jones (sam@example.com)',
            'person': 'The full name of someone in People, optionally with their email in brackets, e.g. Sam Jones (sam@example.com)',
            'helpers': 'Full names of people in People, separated by commas, e.g. Sam Jones, Alex Smith. '
                       'A name can have the email in brackets, e.g. Sam Jones (sam@example.com)',
        }[self.kind]


@dataclass
class ImportType:
    key: str
    label: str
    # the model imported records are, and the index page "View" links to, filtered by ?import_id=
    model: object
    index_endpoint: str
    columns: list
    create: object
    notes: list = field(default_factory=list)
    # called with a row's records and the lookups once the row is saved, so later rows can refer to them
    saved: object = None

    @property
    def slug(self):
        return self.key.replace('_', '-')

    @property
    def delete_permission(self):
        # undo deletes records, so it needs the same permission as deleting them by hand
        return f'{self.key}.delete'


class Lookups:
    """Names the rows refer to, loaded once per import, plus values already used earlier in the file."""

    def __init__(self, person):
        # the signed-in user's person in the organisation being imported into
        self.person = person
        # by lower-case path, which is unique. Locations imported earlier in the file are added as they're saved
        self.locations = {
            location.materialised_path.lower(): location
            for location in Location.query.filter_by(organisation_id=person.organisation_id)
        }
        self.user_groups = {
            user_group.name.strip().lower(): user_group
            for user_group in UserGroup.query.filter_by(organisation_id=person.organisation_id)
        }
        # suppliers and people by lower-case name (a person's full name), loaded when first needed.
        # Names aren't unique, so each is a list
        self.suppliers_by_name = None
        self.people_by_name = None
        self.people_by_email = None
        self.seen = {}
        # each kind of category and condition, by lower-case description, loaded when first needed
        self.references = {}

    def reference_item(self, model, description):
        items = self.references.get(model)
        if items is None:
            items = self.references[model] = {
                item.description.strip().lower(): item
                for item in model.query.filter_by(organisation_id=self.person.organisation_id)
            }
        return items.get(description.lower())

    def by_name(self, kind, name):
        """The suppliers ('suppliers') or people ('people') with this name, ignoring case and spacing."""
        attribute = f'{kind}_by_name'
        if getattr(self, attribute) is None:
            names = {}
            if kind == 'suppliers':
                for supplier in Supplier.query.filter_by(organisation_id=self.person.organisation_id):
                    names.setdefault(' '.join(supplier.name.lower().split()), []).append(supplier)
            else:
                for person in Person.query.filter_by(organisation_id=self.person.organisation_id):
                    names.setdefault(' '.join(person.full_name.lower().split()), []).append(person)
            setattr(self, attribute, names)
        return getattr(self, attribute).get(' '.join(name.lower().split()), [])

    def person_by_email(self, email):
        if self.people_by_email is None:
            self.people_by_email = {
                person.email.lower(): person
                for person in Person.query.filter_by(organisation_id=self.person.organisation_id)
            }
        return self.people_by_email.get(email.lower())

    def people_matching(self, text):
        """The people text names: a full name, or "Name (email)", where the email decides. Raises RowError
        for an email that isn't anyone's here."""
        match = PERSON_WITH_EMAIL.match(text)
        if match is None:
            return self.by_name('people', text)
        person = self.person_by_email(match.group(2))
        if person is None:
            raise RowError(f"There's nobody in People with email '{match.group(2)}'")
        return [person]

    def first_in_file(self, key, value):
        """False if an earlier row in this file already used this value for key."""
        values = self.seen.setdefault(key, set())
        if value in values:
            return False
        values.add(value)
        return True


def parse_value(column, raw, lookups):
    """Turn one cell into the value to store, or raise RowError."""
    if raw == '':
        if column.required:
            raise RowError(f'{column.label} is required')
        return column.blank_means_yes if column.kind == 'yes_no' else None

    if column.kind == 'text':
        if len(raw) > column.max_length:
            raise RowError(f'{column.label} is longer than {column.max_length} characters')
        return raw

    if column.kind == 'address':
        # each comma starts a new line, as on the form's textarea, and the commas are dropped
        address = clean_address(raw, commas=True)
        if address is None and column.required:
            raise RowError(f'{column.label} is required')
        if address and len(address) > column.max_length:
            raise RowError(f'{column.label} is longer than {column.max_length} characters')
        return address

    if column.kind == 'date':
        for date_format in DATE_FORMATS:
            try:
                return datetime.strptime(raw, date_format).date()
            except ValueError:
                pass
        raise RowError(f"{column.label} '{raw}' isn't a date (use dd/mm/yyyy)")

    if column.kind == 'money':
        try:
            value = Decimal(raw.replace('£', '').replace(',', '').strip())
        except InvalidOperation:
            raise RowError(f"{column.label} '{raw}' isn't an amount")
        if not value.is_finite() or value < 0 or value > Decimal('9999999999.99'):
            raise RowError(f'{column.label} must be between 0 and 9,999,999,999.99')
        return value.quantize(Decimal('0.01'))

    if column.kind == 'days':
        try:
            value = Decimal(raw.strip())
        except InvalidOperation:
            raise RowError(f"{column.label} '{raw}' isn't a number of days")
        if not value.is_finite() or value < 0 or value > 9999:
            raise RowError(f'{column.label} must be between 0 and 9,999 days')
        return value.quantize(Decimal('0.01'))

    if column.kind == 'whole_number':
        try:
            return int(raw)
        except ValueError:
            raise RowError(f"{column.label} '{raw}' isn't a whole number")

    if column.kind == 'yes_no':
        if raw.lower() in ('yes', 'y', 'true', '1'):
            return True
        if raw.lower() in ('no', 'n', 'false', '0'):
            return False
        raise RowError(f"{column.label} must be Yes or No, not '{raw}'")

    if column.kind == 'frequency':
        for key, label in FREQUENCIES:
            if raw.lower() in (key, label.lower()):
                return key
        raise RowError(f"{column.label} '{raw}' isn't a frequency")

    if column.kind == 'location':
        # spaces around each '>' and capitals don't matter
        location = lookups.locations.get(normalise_path(raw).lower())
        if location is None:
            raise RowError(f"There's no location '{normalise_path(raw)}'")
        return location

    if column.kind == 'email':
        try:
            email = validate_email(raw, check_deliverability=False).normalized.lower()
        except EmailNotValidError:
            raise RowError(f"'{raw}' isn't a valid email address")
        if len(email) > column.max_length:
            raise RowError(f'{column.label} is longer than {column.max_length} characters')
        return email

    if column.kind == 'job_status':
        # the key (in_progress) or the label (In Progress), ignoring case
        for key, label in JOB_STATUSES:
            if raw.lower() in (key, label.lower()):
                return key
        raise RowError(f"{column.label} '{raw}' isn't a job status")

    if column.kind == 'assignee':
        if PERSON_WITH_EMAIL.match(raw):
            return lookups.people_matching(raw)[0]
        suppliers, people = lookups.by_name('suppliers', raw), lookups.by_name('people', raw)
        matches = suppliers + people
        if not matches:
            raise RowError(f"There's no supplier or person called '{raw}'")
        if len(matches) > 1:
            raise RowError(f"More than one supplier or person is called '{raw}'. For a person, add their email in brackets, "
                           f"e.g. {raw} (name@example.com), or assign the job after importing")
        return matches[0]

    if column.kind == 'person':
        people = lookups.people_matching(raw)
        if not people:
            raise RowError(f"There's nobody in People called '{raw}'")
        if len(people) > 1:
            raise RowError(f"More than one person is called '{raw}'. Add their email in brackets, e.g. {raw} (name@example.com)")
        return people[0]

    if column.kind == 'helpers':
        helpers = []
        # emails have no commas, so "Sam Jones (sam@example.com), Alex Smith" splits into two
        for name in [part.strip() for part in raw.split(',') if part.strip()]:
            people = lookups.people_matching(name)
            if not people:
                raise RowError(f"There's nobody in People called '{name}'")
            if len(people) > 1:
                raise RowError(f"More than one person is called '{name}'. Add their email in brackets, e.g. {name} (name@example.com)")
            if people[0] not in helpers:
                helpers.append(people[0])
        return helpers or None

    if column.kind == 'user_group':
        user_group = lookups.user_groups.get(raw.lower())
        if user_group is None:
            raise RowError(f"There's no user group called '{raw}'")
        if user_group.is_admin and not lookups.person.is_org_admin:
            raise RowError('Only admins can put people in the Admin group')
        return user_group

    if column.kind == 'reference':
        item = lookups.reference_item(column.reference, raw)
        if item is None:
            raise RowError(f"There's no {column.label.lower()} called '{raw}'. Add it under Reference data first")
        return item

    raise ValueError(f'Unknown column kind {column.kind}')


def taken(model, column, value, organisation_id):
    """True if a record in the organisation already has this value, ignoring case."""
    return model.query.filter(
        model.organisation_id == organisation_id,
        db.func.lower(column) == value.lower(),
    ).first() is not None


### CREATE ONE ROW ###
# each takes the parsed values and the lookups, and returns the new records to add, or raises RowError

def address_fields(values):
    return dict(
        name=values['name'],
        address=values['address'],
        telephone=values['telephone'],
    )


def create_location(values, lookups):
    name, parent = values['name'], values['parent']
    if '>' in name:
        raise RowError("Name can't contain '>', which separates the names in a location's path")
    path = build_path(parent, name)
    # paths are unique in the organisation, ignoring case, as on the location form
    if path.lower() in lookups.locations:
        raise RowError(f"There's already a location '{path}'")
    if len(path) > MAX_PATH_LENGTH:
        raise RowError(f'The path is longer than {MAX_PATH_LENGTH} characters')
    # the id is set now so a later row can name this one as its parent
    return [Location(
        id=uuid.uuid4(),
        name=name,
        parent_id=parent.id if parent else None,
        materialised_path=path,
        address=values['address'],
        telephone=values['telephone'],
        email=values['email'],
        location_category_id=reference_id(values['location_category']),
        main_contact_id=reference_id(values['main_contact']),
    )]


def location_saved(records, lookups):
    location = records[0]
    lookups.locations[location.materialised_path.lower()] = location


def reference_id(item):
    return item.id if item else None


def condition_fields(values, key):
    """An asset's condition (column key, e.g. 'asset_condition') and condition_set. A
    condition without a condition_set date was set on the import date, like changing it on the form."""
    condition, condition_set = values[key], values['condition_set']
    if condition_set and condition is None:
        raise RowError(f'Condition set needs {key.replace("_", " ")} as well')
    if condition_set and condition_set > uk_today():
        raise RowError("Condition set can't be in the future")
    return {f'{key}_id': reference_id(condition), 'condition_set': condition_set or (uk_today() if condition else None)}


def create_supplier(values, lookups):
    return [Supplier(**address_fields(values), email=values['email'], supplier_category_id=reference_id(values['supplier_category']))]


def create_asset(values, lookups):
    organisation_id = lookups.person.organisation_id
    for key, column, label in [
        ('asset_number', Asset.asset_number, 'Asset number'),
        ('serial_number', Asset.serial_number, 'Serial number'),
    ]:
        value = values[key]
        if value is None:
            continue
        if not lookups.first_in_file(key, value.lower()):
            raise RowError(f"{label} '{value}' is used by an earlier row in the file")
        if taken(Asset, column, value, organisation_id):
            raise RowError(f"{label} '{value}' is already used by another asset")

    return [Asset(
        description=values['description'],
        location_id=values['location'].id,
        asset_number=values['asset_number'],
        make=values['make'],
        model=values['model'],
        serial_number=values['serial_number'],
        purchase_date=values['purchase_date'],
        purchased_from=values['purchased_from'],
        warranty_expiry=values['warranty_expiry'],
        estimated_replacement_date=values['estimated_replacement_date'],
        estimated_replacement_cost=values['estimated_replacement_cost'],
        asset_category_id=reference_id(values['asset_category']),
        **condition_fields(values, 'asset_condition'),
        assigned_to_id=reference_id(values['assigned_to']),
    )]


def create_planned_job_schedule(values, lookups):
    # the same rule as PlannedJobScheduleForm.validate_notice_days
    notice_days, frequency = values['notice_days'], values['frequency']
    if not 0 <= notice_days <= 365:
        raise RowError('Notice days must be between 0 and 365')
    if notice_days > FREQUENCY_MIN_DAYS[frequency]:
        raise RowError(f"Notice days can't be longer than the time between jobs "
                       f"({FREQUENCY_MIN_DAYS[frequency]} days for {FREQUENCY_LABELS[frequency].lower()})")

    return [PlannedJobSchedule(
        description=values['description'],
        work_required=values['work_required'],
        location_id=values['location'].id,
        next_due_by=values['next_due_by'],
        frequency=frequency,
        notice_days=notice_days,
        next_job_creation_date=job_creation_date(values['next_due_by'], notice_days),
        statutory_compliance=values['statutory_compliance'],
        is_active=values['active'],
        job_category_id=reference_id(values['job_category']),
        estimated_cost=values['estimated_cost'],
        estimated_duration=values['estimated_duration'],
    )]


def create_job(values, lookups):
    """An imported job is reactive (it isn't raised from a schedule). The status can be any of the job
    statuses, but it has to make sense with the assignee: assigned, in progress, completed and closed jobs
    need one, and new, unassigned and rejected jobs can't have one, or helpers. Nobody is notified, and the
    dates the workflow buttons normally record (approved, assigned, started, completed, closed) are left blank."""
    status = values['status'] or 'new'
    assignee, helpers = values['assigned_to'], values['helpers'] or []
    has_assignee = status in ASSIGNED_JOB_STATUSES or status == 'closed'
    if has_assignee and assignee is None:
        raise RowError(f"A job with status {dict(JOB_STATUSES)[status]} needs assigned to")
    if not has_assignee and (assignee is not None or helpers):
        raise RowError(f"A job with status {dict(JOB_STATUSES)[status]} can't have assigned to or helpers. Use Assigned (or later) as the status")
    if isinstance(assignee, Person) and assignee in helpers:
        raise RowError(f"{assignee.full_name} is assigned the job, so can't also be a helper")

    job = Job(
        description=values['description'],
        work_required=values['work_required'],
        location_id=values['location'].id,
        due_by=values['due_by'],
        status=status,
        source='reactive',
        statutory_compliance=values['statutory_compliance'],
        job_category_id=reference_id(values['job_category']),
        estimated_cost=values['estimated_cost'],
        actual_cost=values['actual_cost'],
        assigned_supplier_id=assignee.id if isinstance(assignee, Supplier) else None,
        assigned_person_id=assignee.id if isinstance(assignee, Person) else None,
    )
    # added by job_saved once the job is saved: setting the relationship now would put the job in the
    # session before its row's savepoint
    job.import_helpers = helpers
    return [job]


def job_saved(records, lookups):
    job = records[0]
    job.helpers = job.import_helpers


def create_person(values, lookups):
    email = values['email']
    if not lookups.first_in_file('email', email):
        raise RowError(f"Email '{email}' is used by an earlier row in the file")
    # one person per email in an organisation, as on the person form
    if Person.query.filter(Person.organisation_id == lookups.person.organisation_id, Person.email == email).first():
        raise RowError(f"Someone with email '{email}' is already in your organisation")

    # an email with a login elsewhere (another organisation) shares it, like the person form. Imported
    # people can't log in until someone turns it on, so nobody is emailed by an import
    user = User.query.filter(User.email == email).first()
    records = []
    if user is None:
        user = User(email=email)
        records.append(user)
    user_group = values['user_group']
    person = Person(
        first_name=values['first_name'],
        last_name=values['last_name'],
        email=email,
        job_title=values['job_title'],
        mobile=values['mobile'],
        can_login=False,
        user_group_id=user_group.id if user_group else None,
        user_id=user.id,
    )
    if user.id is None:
        person.user = user
    return records + [person]


def text(key, max_length, required=False, notes=''):
    return Column(key, 'text', required=required, max_length=max_length, notes=notes)


ADDRESS_COLUMNS = [
    text('name', 100, required=True),
    Column('address', 'address', max_length=MAX_ADDRESS_LENGTH),
    text('telephone', 100),
]

def reference(key, model, notes=''):
    return Column(key, 'reference', reference=model, notes=notes or 'Optional. Must match one you already have, ignoring capitals')


CONDITION_SET_NOTES = 'Optional. The date the condition was set. Blank means the import date, when there is a condition'

LOCATION_COLUMN = Column('location', 'location', required=True,
                         notes='The full path of an existing location, e.g. Head Office > Main Building. Spaces around > and capitals don\'t matter. Import locations first')

# in the order they're offered. Locations come first because the others refer to them
IMPORT_TYPES = {import_type.key: import_type for import_type in [
    ImportType('locations', 'Locations', Location, 'locations.index', [
        text('name', 100, required=True, notes="Can't contain >"),
        Column('parent', 'location', notes='Optional: the full path of the location this one is inside, e.g. Head Office > Main Building. '
                                           'It can be a location added by an earlier row in the file. Blank makes a top-level location'),
        *ADDRESS_COLUMNS[1:],
        Column('email', 'email', max_length=255),
        reference('location_category', LocationCategory),
        Column('main_contact', 'person', notes='Optional. Someone in People, ignoring capitals. Add the email in brackets '
                                               'when two people share a name, as the Excel template\'s dropdown does. Import people first'),
    ], create_location, notes=[
        "A location's path (its parent's path, then its name) must be new to your organisation, ignoring capitals.",
        'Put each location after the row for its parent location.',
    ], saved=location_saved),
    ImportType('assets', 'Assets', Asset, 'assets.index', [
        text('description', 50, required=True),
        LOCATION_COLUMN,
        text('asset_number', 50, notes='Unique within your organisation, ignoring case'),
        text('make', 50),
        text('model', 50),
        text('serial_number', 50, notes='Unique within your organisation, ignoring case'),
        Column('purchase_date', 'date'),
        text('purchased_from', 100),
        Column('warranty_expiry', 'date'),
        Column('estimated_replacement_date', 'date'),
        Column('estimated_replacement_cost', 'money'),
        reference('asset_category', AssetCategory),
        reference('asset_condition', AssetCondition),
        Column('condition_set', 'date', notes=CONDITION_SET_NOTES),
        Column('assigned_to', 'person', notes='Optional. The person the asset is assigned to: someone in People, ignoring capitals. '
                                              'Add the email in brackets when two people share a name, as the Excel template\'s dropdown does. Import people first'),
    ], create_asset),
    ImportType('people', 'People', Person, 'people.index', [
        text('first_name', 100, required=True),
        text('last_name', 100, required=True),
        Column('email', 'email', required=True, max_length=255, notes='Unique: nobody else, in any organisation, can have the same email'),
        text('job_title', 100),
        text('mobile', 100),
        Column('user_group', 'user_group', notes='Only admins can use the Admin group'),
    ], create_person, notes=[
        "Imported people can't log in, and aren't emailed. Turn on \"Can log in\" on a person's record to invite them.",
    ]),
    ImportType('suppliers', 'Suppliers', Supplier, 'suppliers.index', [
        *ADDRESS_COLUMNS,
        Column('email', 'email', max_length=255),
        reference('supplier_category', SupplierCategory),
    ], create_supplier),
    ImportType('planned_jobs', 'Planned Jobs', PlannedJobSchedule, 'planned_jobs.index', [
        text('description', 100, required=True),
        text('work_required', 500, required=True),
        LOCATION_COLUMN,
        Column('next_due_by', 'date', required=True),
        Column('frequency', 'frequency', required=True, notes='The short form also works, e.g. monthly or three_monthly'),
        Column('notice_days', 'whole_number', required=True,
               notes="0 to 365, and no more than the gap between jobs (e.g. 28 for monthly). The next job creation date is worked out from it"),
        Column('statutory_compliance', 'yes_no'),
        Column('active', 'yes_no', blank_means_yes=True, notes="Inactive schedules don't raise any jobs until they're made active"),
        reference('job_category', JobCategory, notes='Optional. Must match one you already have, ignoring capitals. Jobs raised from the schedule get it too'),
        Column('estimated_cost', 'money', notes='Optional. Jobs raised from the schedule get it too'),
        Column('estimated_duration', 'days'),
    ], create_planned_job_schedule),
    ImportType('jobs', 'Jobs', Job, 'jobs.index', [
        text('description', 100, required=True),
        text('work_required', 500, required=True),
        LOCATION_COLUMN,
        Column('due_by', 'date'),
        Column('status', 'job_status', notes='Optional. Assigned, In Progress, Completed and Closed jobs need assigned to; New, Unassigned and Rejected jobs must leave assigned to and helpers blank'),
        Column('assigned_to', 'assignee', notes='Optional. Must match exactly one supplier or person, ignoring capitals. '
                                                'Add the email in brackets when two people share a name. Import suppliers and people first'),
        Column('helpers', 'helpers', notes="Optional. Each name must match exactly one person, ignoring capitals, and can't be the person in assigned to. "
                                           "The Excel template has no dropdown here, as a cell can hold several names"),
        reference('job_category', JobCategory),
        Column('statutory_compliance', 'yes_no'),
        Column('estimated_cost', 'money'),
        Column('actual_cost', 'money'),
    ], create_job, notes=[
        'Imported jobs are reactive jobs. Jobs raised from a planned job schedule come from the schedule, so they can\'t be imported.',
        "Nobody is notified about imported jobs, including the people and suppliers they're assigned to.",
        "The dates the job's buttons normally record (approved, assigned, started, completed and closed) are left blank.",
    ], saved=job_saved),
]}


def normalise_header(header):
    return header.strip().lower().replace(' ', '_')


def read_file(import_type, data):
    """Return (rows, problems) for an uploaded .xlsx (the first sheet). rows is a list of
    (row number, {key: cell}); problems is a list of (header, problem) that stop the import."""
    if len(data) > MAX_BYTES:
        return [], [('', f'The file is bigger than {MAX_BYTES // (1024 * 1024)} MB. Split it into smaller files.')]

    try:
        lines = read_xlsx(data)
    except SpreadsheetError as error:
        return [], [('', str(error))]
    return read_rows(import_type, lines)


def read_rows(import_type, lines):
    """The header checks and rows for read_file. lines is a list of rows, each a list of text cells."""
    if not lines:
        return [], [('', 'The file is empty.')]
    headers = lines[0]

    keys = [normalise_header(header) for header in headers]
    known = {column.key for column in import_type.columns}
    problems = []
    for header, key in zip(headers, keys):
        if key == '':
            problems.append((header, 'A column has no header.'))
        elif key not in known:
            problems.append((header, f'Not a {import_type.label.lower()} column.'))
    for key in sorted({key for key in keys if key and keys.count(key) > 1}):
        problems.append((key, 'This column appears more than once.'))
    for column in import_type.columns:
        if column.required and column.key not in keys:
            problems.append((column.key, 'This column is required but missing.'))
    if problems:
        return [], problems

    rows = []
    for row_number, cells in enumerate(lines[1:], start=2):
        cells = [cell.strip() for cell in cells]
        if not any(cells):
            continue
        if len(rows) == MAX_ROWS:
            return [], [('', f'The file has more than {MAX_ROWS} rows. Split it into smaller files.')]
        # missing cells at the end count as blank, extra ones are ignored
        cells += [''] * (len(keys) - len(cells))
        rows.append((row_number, dict(zip(keys, cells))))

    if not rows:
        return [], [('', 'The file has headers but no rows.')]
    return rows, []


def run_import(import_type, filename, rows, person):
    """Save every row that's valid, and record the import with the errors for the rest. person is the
    signed-in user's person in the organisation. Commits."""
    import_event = ImportEvent(
        import_type=import_type.key,
        filename=filename[:255],
        organisation_id=person.organisation_id,
        imported_by_name=person.full_name,
    )
    db.session.add(import_event)
    db.session.flush()

    lookups = Lookups(person)
    imported = 0
    errors = []
    for row_number, cells in rows:
        values = {}
        problems = []
        for column in import_type.columns:
            try:
                values[column.key] = parse_value(column, cells.get(column.key, ''), lookups)
            except RowError as error:
                problems.append(str(error))
        if problems:
            errors.append({'row': row_number, 'error': '; '.join(problems)})
            continue

        try:
            records = import_type.create(values, lookups)
            # a savepoint per row, so a row the database refuses doesn't undo the others.
            # Records link to locations and groups by id, so nothing joins the session before this
            with db.session.begin_nested():
                for record in records:
                    # a new User (for a person) belongs to no organisation
                    if hasattr(record, 'organisation_id'):
                        record.organisation_id = person.organisation_id
                    if hasattr(record, 'import_event_id'):
                        record.import_event_id = import_event.id
                    db.session.add(record)
        except RowError as error:
            errors.append({'row': row_number, 'error': str(error)})
            continue
        except IntegrityError:
            errors.append({'row': row_number, 'error': "The database refused this row. It may duplicate an existing record."})
            continue
        if import_type.saved:
            import_type.saved(records, lookups)
        imported += 1

    import_event.rows_imported = imported
    import_event.errors = errors
    db.session.commit()
    return import_event


# the Excel format typed values get in each kind of template column. '@' is text, so 00123 or a phone
# number keeps its leading zeros
TEMPLATE_FORMATS = {'date': 'dd/mm/yyyy', 'money': '#,##0.00', 'whole_number': '0', 'days': '0.00', 'yes_no': 'General'}


def column_choices(column, person):
    """The template dropdown for a column, as (choices, strict), or (None, False) for none. Lists of the
    organisation's records only include what the user can already see, and only warn, since the list can be
    out of date by the time the file is imported (the import checks every value anyway)."""
    organisation_id = person.organisation_id
    if column.kind == 'yes_no':
        return ['Yes', 'No'], True
    if column.kind == 'frequency':
        return [label for _, label in FREQUENCIES], True
    if column.kind == 'job_status':
        return [label for _, label in JOB_STATUSES], True
    if column.kind == 'location':
        if not can(person, 'locations.view'):
            return None, False
        locations = Location.query.filter_by(organisation_id=organisation_id).order_by(db.func.lower(Location.materialised_path))
        return [location.materialised_path for location in locations], False
    if column.kind == 'reference':
        model = column.reference
        items = model.query.filter_by(organisation_id=organisation_id).order_by(db.func.lower(model.description))
        return [item.description for item in items], False
    if column.kind == 'user_group':
        groups = UserGroup.query.filter_by(organisation_id=organisation_id).order_by(db.func.lower(UserGroup.name))
        # only admins can put people in the Admin group
        return [group.name for group in groups if person.is_org_admin or not group.is_admin], False
    if column.kind == 'person':
        if not can(person, 'people.view'):
            return None, False
        return people_choices(organisation_id), False
    if column.kind == 'assignee':
        choices = []
        if can(person, 'suppliers.view'):
            suppliers = Supplier.query.filter_by(organisation_id=organisation_id).order_by(db.func.lower(Supplier.name))
            choices += [supplier.name for supplier in suppliers]
        if can(person, 'people.view'):
            choices += people_choices(organisation_id)
        return choices, False
    return None, False


def people_choices(organisation_id):
    """Everyone in the organisation as "Name (email)", so people who share a name can be told apart."""
    people = Person.query.filter_by(organisation_id=organisation_id).order_by(
        db.func.lower(Person.first_name), db.func.lower(Person.last_name))
    return [f'{p.full_name} ({p.email})' for p in people]


def template_width(column):
    if column.kind in ('location', 'assignee', 'helpers', 'person', 'address') or (column.max_length or 0) >= 500:
        return 50
    return max(len(column.key) + 4, 18)


def template_workbook(import_type, person):
    """The .xlsx template: the header row with a note on each header, column formats, and dropdowns of
    the choices (see column_choices). person is the signed-in user's person."""
    columns = []
    for column in import_type.columns:
        choices, strict = column_choices(column, person)
        comment = ('Required. ' if column.required else 'Optional. ') + column.format
        if column.notes:
            comment += '\n\n' + column.notes
        columns.append(dict(
            key=column.key,
            comment=comment,
            width=template_width(column),
            number_format=TEMPLATE_FORMATS.get(column.kind, '@'),
            choices=choices,
            strict=strict,
        ))
    return template_xlsx(import_type.label, columns)


### UNDO ###

def remaining_records(import_type, import_event):
    """The import's records that still exist. Some may have been deleted by hand since."""
    return import_type.model.query.filter_by(import_event_id=import_event.id).all()


def undo_blockers(import_type, import_event, person):
    """Reasons the import can't be undone, so deleting its records never takes anything else with it.
    Empty means it can be."""
    model = import_type.model
    query = model.query.filter_by(import_event_id=import_event.id)
    blockers = []

    if model is Location:
        # deleting a location deletes the locations in it and the assets, jobs and planned job schedules at them.
        # Imported locations inside an imported one are checked themselves
        count = query.filter(db.or_(Location.assets.any(), Location.jobs.any(), Location.planned_job_schedules.any())).count()
        if count:
            blockers.append(f'{count} of the imported locations now have assets, jobs or planned job schedules. '
                            'Delete or move those first, or undo the imports that added them.')
        other = Location.query.filter(
            Location.parent_id.in_(db.session.query(Location.id).filter(Location.import_event_id == import_event.id)),
            db.or_(Location.import_event_id.is_(None), Location.import_event_id != import_event.id),
        ).count()
        if other:
            blockers.append(f"{other} location(s) that weren't in this import have been added inside the imported ones. "
                            'Delete or move those first.')

    if model is PlannedJobSchedule:
        # Job.planned_job_schedule_id is ondelete RESTRICT
        count = query.filter(PlannedJobSchedule.jobs.any()).count()
        if count:
            blockers.append(f'{count} of the imported planned job schedules have had jobs raised from them.')

    if model is Person:
        count = query.filter(Person.can_login.is_(True)).count()
        if count:
            blockers.append(f'{count} of the imported people can now log in. '
                            'Turn off "Can log in" on their records first.')
        # the same rule as the people pages: only admins can delete an admin
        if not person.is_org_admin and query.join(UserGroup, Person.user_group_id == UserGroup.id).filter(UserGroup.is_admin.is_(True)).count():
            blockers.append('Only admins can undo an import that put people in the Admin group.')

    # a job's assignee is kept for audit, so a supplier or person with jobs assigned can't be deleted
    if model in (Person, Supplier):
        count = query.filter(model.assigned_jobs.any()).count()
        if count:
            blockers.append(f'{count} of the imported {import_type.label.lower()} have jobs assigned to them, '
                            'which are kept for audit purposes.')

    return blockers


def undo_import(import_type, import_event, person):
    """Delete the import's remaining records and mark it undone. Commits. Returns how many were deleted."""
    records = remaining_records(import_type, import_event)
    for record in records:
        # like the people delete route, a person's user goes too unless it's in another organisation
        if isinstance(record, Person):
            delete_person(record)
        else:
            db.session.delete(record)

    import_event.undone_at = datetime.now(timezone.utc)
    import_event.undone_by_name = person.full_name
    db.session.commit()
    return len(records)
