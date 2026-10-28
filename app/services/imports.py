import csv
import io
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from email_validator import EmailNotValidError, validate_email
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Asset, Building, ImportEvent, JobSchedule, Person, Site, Supplier, User, UserGroup, Zone, JobCategory, AssetCategory, AssetCondition, BuildingCondition, SupplierCategory
from app.services.people import delete_person
from app.services.scheduling import FREQUENCIES, FREQUENCY_LABELS, FREQUENCY_MIN_DAYS, job_creation_date, uk_today

# CSV imports. Each import type lists its columns; the header row must use the column keys.
# A file is refused outright if its headers are wrong. Otherwise every row is checked and saved
# on its own, so good rows are imported and bad ones are reported with the reason

MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 5000
DATE_FORMATS = ('%d/%m/%Y', '%d-%m-%Y', '%Y-%m-%d')


class RowError(Exception):
    pass


@dataclass
class Column:
    key: str
    kind: str  # text, date, money, whole_number, days, yes_no, frequency, site, email, user_group, reference
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
        if self.kind == 'reference':
            return f'The description of one of your {self.label.lower().replace("category", "categories").replace("condition", "conditions")} (Reference data)'
        return {
            'date': 'Date: dd/mm/yyyy, dd-mm-yyyy or yyyy-mm-dd',
            'money': 'Amount in pounds, e.g. 1250.00 (£ and commas are allowed)',
            'whole_number': 'Whole number',
            'days': 'Number of days, e.g. 0.5 for half a day',
            'yes_no': f"Yes or No. Blank means {'Yes' if self.blank_means_yes else 'No'}",
            'frequency': 'One of: ' + ', '.join(label for _, label in FREQUENCIES),
            'site': 'The name of an existing site',
            'email': f'Email address, up to {self.max_length} characters',
            'user_group': 'The name of an existing user group',
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
        self.sites = {}
        for site in Site.query.filter_by(organisation_id=person.organisation_id):
            self.sites.setdefault(site.name.strip().lower(), []).append(site)
        self.user_groups = {
            user_group.name.strip().lower(): user_group
            for user_group in UserGroup.query.filter_by(organisation_id=person.organisation_id)
        }
        # by (site id, lower-case name) and (building id, lower-case name). Names are unique there
        self.buildings = {
            (building.site_id, building.name.strip().lower()): building
            for building in Building.query.filter_by(organisation_id=person.organisation_id)
        }
        self.zones = {
            (zone.building_id, zone.name.strip().lower()): zone
            for zone in Zone.query.filter_by(organisation_id=person.organisation_id)
        }
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

    if column.kind == 'site':
        sites = lookups.sites.get(raw.lower(), [])
        if not sites:
            raise RowError(f"There's no site called '{raw}'")
        if len(sites) > 1:
            raise RowError(f"More than one site is called '{raw}'")
        return sites[0]

    if column.kind == 'email':
        try:
            email = validate_email(raw, check_deliverability=False).normalized.lower()
        except EmailNotValidError:
            raise RowError(f"'{raw}' isn't a valid email address")
        if len(email) > column.max_length:
            raise RowError(f'{column.label} is longer than {column.max_length} characters')
        return email

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
        address_line_one=values['address_line_one'],
        address_line_two=values['address_line_two'],
        address_line_three=values['address_line_three'],
        town_city=values['town_city'],
        county=values['county'],
        post_code=values['post_code'],
        telephone=values['telephone'],
    )


def create_site(values, lookups):
    # names are unique in the organisation, ignoring case, as on the site form
    name = values['name']
    if lookups.sites.get(name.lower()):
        raise RowError(f"There's already a site called '{name}'")
    if not lookups.first_in_file('site', name.lower()):
        raise RowError(f"Site '{name}' is used by an earlier row in the file")
    return [Site(**address_fields(values))]


def find_building(site, name, lookups):
    building = lookups.buildings.get((site.id, name.lower()))
    if building is None:
        raise RowError(f"{site.name} has no building called '{name}'")
    return building


def location(values, lookups):
    """The row's optional building (at its site) and zone (in that building) as ids, or raise RowError."""
    building = zone = None
    if values['building']:
        building = find_building(values['site'], values['building'], lookups)
    if values['zone']:
        if building is None:
            raise RowError('A zone needs a building as well')
        zone = lookups.zones.get((building.id, values['zone'].lower()))
        if zone is None:
            raise RowError(f"{building.name} has no zone called '{values['zone']}'")
    return dict(building_id=building.id if building else None, zone_id=zone.id if zone else None)


def reference_id(item):
    return item.id if item else None


def condition_fields(values, key):
    """An asset's or building's condition (column key, e.g. 'asset_condition') and condition_set. A
    condition without a condition_set date was set on the import date, like changing it on the form."""
    condition, condition_set = values[key], values['condition_set']
    if condition_set and condition is None:
        raise RowError(f'Condition set needs {key.replace("_", " ")} as well')
    if condition_set and condition_set > uk_today():
        raise RowError("Condition set can't be in the future")
    return {f'{key}_id': reference_id(condition), 'condition_set': condition_set or (uk_today() if condition else None)}


def create_building(values, lookups):
    site, name = values['site'], values['name']
    # unique within the site, ignoring case, as on the building form
    if (site.id, name.lower()) in lookups.buildings:
        raise RowError(f"{site.name} already has a building called '{name}'")
    if not lookups.first_in_file('building', (site.id, name.lower())):
        raise RowError(f"Building '{name}' at {site.name} is used by an earlier row in the file")
    return [Building(name=name, site_id=site.id, **condition_fields(values, 'building_condition'))]


def create_zone(values, lookups):
    building, name = find_building(values['site'], values['building'], lookups), values['name']
    # unique within the building, ignoring case, as on the zone form
    if (building.id, name.lower()) in lookups.zones:
        raise RowError(f"{building.name} already has a zone called '{name}'")
    if not lookups.first_in_file('zone', (building.id, name.lower())):
        raise RowError(f"Zone '{name}' in {building.name} is used by an earlier row in the file")
    return [Zone(name=name, building_id=building.id)]


def create_supplier(values, lookups):
    return [Supplier(**address_fields(values), supplier_category_id=reference_id(values['supplier_category']))]


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
        site_id=values['site'].id,
        **location(values, lookups),
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
    )]


def create_job_schedule(values, lookups):
    # the same rule as JobScheduleForm.validate_notice_days
    notice_days, frequency = values['notice_days'], values['frequency']
    if not 0 <= notice_days <= 365:
        raise RowError('Notice days must be between 0 and 365')
    if notice_days > FREQUENCY_MIN_DAYS[frequency]:
        raise RowError(f"Notice days can't be longer than the time between jobs "
                       f"({FREQUENCY_MIN_DAYS[frequency]} days for {FREQUENCY_LABELS[frequency].lower()})")

    return [JobSchedule(
        description=values['description'],
        work_required=values['work_required'],
        site_id=values['site'].id,
        **location(values, lookups),
        next_due=values['next_due'],
        frequency=frequency,
        notice_days=notice_days,
        next_job_creation_date=job_creation_date(values['next_due'], notice_days),
        statutory_compliance=values['statutory_compliance'],
        is_active=values['active'],
        job_category_id=reference_id(values['job_category']),
        estimated_cost=values['estimated_cost'],
        estimated_duration=values['estimated_duration'],
    )]


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
    text('address_line_one', 100),
    text('address_line_two', 100),
    text('address_line_three', 100),
    text('town_city', 100),
    text('county', 100),
    text('post_code', 100),
    text('telephone', 100),
]

def reference(key, model, notes=''):
    return Column(key, 'reference', reference=model, notes=notes or 'Optional. Must match one you already have, ignoring capitals')


CONDITION_SET_NOTES = 'Optional. The date the condition was set. Blank means the import date, when there is a condition'

LOCATION_COLUMNS = [
    text('building', 100, notes="Optional. The name of a building at the row's site. Import buildings first"),
    text('zone', 100, notes='Optional. The name of a zone in that building. Needs a building'),
]

# in the order they're offered. Sites, buildings and zones come first because the others refer to them
IMPORT_TYPES = {import_type.key: import_type for import_type in [
    ImportType('sites', 'Sites', Site, 'sites.index', [
        text('name', 100, required=True, notes='Unique within your organisation, ignoring case'),
        *ADDRESS_COLUMNS[1:],
    ], create_site),
    ImportType('buildings', 'Buildings', Building, 'buildings.index', [
        text('name', 100, required=True, notes='Unique within the site, ignoring case'),
        Column('site', 'site', required=True, notes='Import sites first'),
        reference('building_condition', BuildingCondition),
        Column('condition_set', 'date', notes=CONDITION_SET_NOTES),
    ], create_building),
    ImportType('zones', 'Zones', Zone, 'zones.index', [
        text('name', 100, required=True, notes='Unique within the building, ignoring case'),
        Column('site', 'site', required=True, notes='The site the building is at'),
        text('building', 100, required=True, notes='The name of a building at that site. Import buildings first'),
    ], create_zone),
    ImportType('assets', 'Assets', Asset, 'assets.index', [
        text('description', 50, required=True),
        Column('site', 'site', required=True, notes='Import sites first'),
        *LOCATION_COLUMNS,
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
        reference('supplier_category', SupplierCategory),
    ], create_supplier),
    ImportType('job_schedules', 'Job Schedules', JobSchedule, 'job_schedules.index', [
        text('description', 100, required=True),
        text('work_required', 500, required=True),
        Column('site', 'site', required=True, notes='Import sites first'),
        *LOCATION_COLUMNS,
        Column('next_due', 'date', required=True),
        Column('frequency', 'frequency', required=True, notes='The short form also works, e.g. monthly or three_monthly'),
        Column('notice_days', 'whole_number', required=True,
               notes="0 to 365, and no more than the gap between jobs (e.g. 28 for monthly). The next job creation date is worked out from it"),
        Column('statutory_compliance', 'yes_no'),
        Column('active', 'yes_no', blank_means_yes=True, notes="Inactive schedules don't raise any jobs until they're made active"),
        reference('job_category', JobCategory, notes='Optional. Must match one you already have, ignoring capitals. Jobs raised from the schedule get it too'),
        Column('estimated_cost', 'money', notes='Optional. Jobs raised from the schedule get it too'),
        Column('estimated_duration', 'days'),
    ], create_job_schedule),
]}


def decode(data):
    """CSV bytes as text. Excel saves UTF-8 with a BOM, or Windows-1252 for "CSV (Comma delimited)"."""
    try:
        return data.decode('utf-8-sig')
    except UnicodeDecodeError:
        return data.decode('cp1252', errors='replace')


def normalise_header(header):
    return header.strip().lower().replace(' ', '_')


def read_csv(import_type, data):
    """Return (rows, problems). rows is a list of (row number, {key: cell}); problems is a list of
    (header, problem) that stop the import."""
    if len(data) > MAX_BYTES:
        return [], [('', f'The file is bigger than {MAX_BYTES // (1024 * 1024)} MB. Split it into smaller files.')]

    reader = csv.reader(io.StringIO(decode(data)))
    try:
        headers = next(reader)
    except StopIteration:
        return [], [('', 'The file is empty.')]
    except csv.Error as error:
        return [], [('', f"The file couldn't be read as CSV: {error}")]

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
    try:
        for row_number, cells in enumerate(reader, start=2):
            cells = [cell.strip() for cell in cells]
            if not any(cells):
                continue
            if len(rows) == MAX_ROWS:
                return [], [('', f'The file has more than {MAX_ROWS} rows. Split it into smaller files.')]
            # missing cells at the end count as blank, extra ones are ignored
            cells += [''] * (len(keys) - len(cells))
            rows.append((row_number, dict(zip(keys, cells))))
    except csv.Error as error:
        return [], [('', f"The file couldn't be read as CSV: {error}")]

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
        person_id=person.id,
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
            # Records link to sites and groups by id, so nothing joins the session before this
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
        imported += 1

    import_event.rows_imported = imported
    import_event.errors = errors
    db.session.commit()
    return import_event


def template_csv(import_type):
    """A file with just the header row, to fill in."""
    output = io.StringIO()
    csv.writer(output).writerow([column.key for column in import_type.columns])
    return '﻿' + output.getvalue()


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

    if model is Site:
        # deleting a site deletes its buildings, assets, jobs and job schedules, imported or not
        count = query.filter(db.or_(Site.buildings.any(), Site.assets.any(), Site.jobs.any(), Site.job_schedules.any())).count()
        if count:
            blockers.append(f'{count} of the imported sites now have buildings, assets, jobs or job schedules. '
                            'Delete those first, or undo the imports that added them.')

    if model is Building:
        # deleting a building deletes its zones, and takes it off assets, jobs and job schedules
        count = query.filter(db.or_(Building.zones.any(), Building.assets.any(), Building.jobs.any(), Building.job_schedules.any())).count()
        if count:
            blockers.append(f'{count} of the imported buildings now have zones, assets, jobs or job schedules. '
                            'Delete or move those first, or undo the imports that added them.')

    if model is Zone:
        count = query.filter(db.or_(Zone.assets.any(), Zone.jobs.any(), Zone.job_schedules.any())).count()
        if count:
            blockers.append(f'{count} of the imported zones now have assets, jobs or job schedules in them. '
                            'Move those first, or undo the imports that added them.')

    if model is JobSchedule:
        # Job.job_schedule_id is ondelete RESTRICT
        count = query.filter(JobSchedule.jobs.any()).count()
        if count:
            blockers.append(f'{count} of the imported job schedules have had jobs raised from them.')

    if model is Person:
        count = query.filter(Person.can_login.is_(True)).count()
        if count:
            blockers.append(f'{count} of the imported people can now log in. '
                            'Turn off "Can log in" on their records first.')
        # the same rule as the people pages: only admins can delete an admin
        if not person.is_org_admin and query.join(UserGroup, Person.user_group_id == UserGroup.id).filter(UserGroup.is_admin.is_(True)).count():
            blockers.append('Only admins can undo an import that put people in the Admin group.')

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
    import_event.undone_by_id = person.id
    db.session.commit()
    return len(records)
