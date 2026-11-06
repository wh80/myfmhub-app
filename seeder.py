"""Populate the database with random test data.

Usage:
    python seeder.py                # add 50 of each record type
    python seeder.py --count 100    # add 100 of each record type
    python seeder.py --clear        # delete all existing data first

Logging In To Use Seeded Data

    To login check db for created users
    Login using selected users email - OTP code will show in the console
    NB: Comment out Resend KEY in .env to avoid sending emails to seeded users

    Seeding again without --clear adds another organisation. Up to two people who can log in to an
    existing organisation are added to it too, so Switch organisation can be tried


"""
import argparse
import random
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app import create_app
from app.extensions import db
from app.services.locations import build_path
from app.services.permissions import create_default_groups
from app.services.scheduling import FREQUENCIES, job_creation_date
from app.models import ASSIGNED_JOB_STATUSES, JOB_STATUSES, ImportEvent, Organisation, User, Person, OTP, UserSession, UserGroup, UserGroupPermission, Location, Asset, Job, PlannedJobSchedule, Supplier, Notification, job_assets, job_helpers, planned_job_schedule_assets
from app.models import JobCategory, AssetCategory, AssetCondition, LocationCategory, SupplierCategory, DocumentCategory

PLACE_NAMES = ['Riverside', 'Oakwood', 'Hillcrest', 'Kingsway', 'Meadow', 'Harbour', 'Station', 'Market', 'Abbey', 'Victoria']
BUILDING_NAMES = ['Main Building', 'North Block', 'South Block', 'East Wing', 'West Wing', 'Annex', 'Plant House', 'Gatehouse']
ZONE_NAMES = ['Basement', 'Ground Floor', 'First Floor', 'Second Floor', 'Roof', 'Plant Room', 'Kitchen', 'Reception']
SITE_TYPES = ['House', 'Court', 'Centre', 'Park', 'Plaza', 'Tower', 'Depot', 'Campus', 'Works', 'Hall']
STREETS = ['High Street', 'Church Road', 'Mill Lane', 'Park Avenue', 'London Road', 'Queen Street', 'Green Lane', 'Station Road']
TOWNS = ['Leeds', 'Bristol', 'Manchester', 'Birmingham', 'Sheffield', 'Nottingham', 'Cardiff', 'Glasgow', 'Newcastle', 'York']
# each organisation's reference data (services/reference_data.py)
REFERENCE_DATA = {
    JobCategory: ['Electrical', 'Mechanical', 'Plumbing', 'Fire Safety', 'Building Fabric', 'Grounds'],
    AssetCategory: ['HVAC', 'Electrical', 'Fire Safety', 'Lifts', 'Plumbing', 'Security'],
    AssetCondition: ['Excellent', 'Good', 'Fair', 'Poor', 'Unserviceable'],
    LocationCategory: ['Site', 'Building', 'Floor', 'Room'],
    SupplierCategory: ['Electrical Contractor', 'Mechanical Contractor', 'Cleaning', 'Security', 'Grounds Maintenance'],
    DocumentCategory: ['Certificate', 'Manual', 'Photo', 'Report', 'Contract'],
}
COUNTIES = ['West Yorkshire', 'Somerset', 'Greater Manchester', 'West Midlands', 'South Yorkshire', 'Nottinghamshire', 'Glamorgan', 'Lanarkshire', 'Tyne and Wear', 'North Yorkshire']
POST_CODES = ['LS1', 'BS1', 'M1', 'B1', 'S1', 'NG1', 'CF10', 'G1', 'NE1', 'YO1']

SUPPLIER_PREFIXES = ['Apex', 'Premier', 'Reliable', 'Northern', 'City', 'Total', 'Prime', 'Allied', 'Crown', 'Swift']
SUPPLIER_TRADES = ['Mechanical', 'Electrical', 'Plumbing', 'Fire Safety', 'Lift Services', 'HVAC', 'Roofing', 'Security', 'Cleaning', 'Building Services']
SUPPLIER_SUFFIXES = ['Ltd', 'Group', 'Services Ltd', '& Sons', 'Solutions']

# (asset description, makes)
ASSET_TYPES = [
    ('Boiler', ['Worcester Bosch', 'Vaillant', 'Ideal', 'Baxi']),
    ('Air Handling Unit', ['Daikin', 'Trane', 'Carrier']),
    ('Chiller', ['Daikin', 'Trane', 'Carrier']),
    ('Fire Alarm Panel', ['Gent', 'Kentec', 'Advanced']),
    ('Passenger Lift', ['Otis', 'Kone', 'Schindler']),
    ('Emergency Lighting', ['Eaton', 'Thorn', 'Ansell']),
    ('Water Heater', ['Andrews', 'Rinnai', 'Lochinvar']),
    ('Generator', ['Caterpillar', 'Cummins', 'Kohler']),
    ('Fire Door', ['Leaderflush', 'Jeld-Wen', 'Premdor']),
    ('Booster Pump', ['Grundfos', 'Wilo', 'DAB']),
]
LOCATIONS = ['Basement', 'Ground Floor', 'First Floor', 'Roof', 'Plant Room', 'Car Park', 'Kitchen', 'Reception']

FIRST_NAMES = ['Alice', 'Ben', 'Chloe', 'Dan', 'Emma', 'Farah', 'George', 'Hannah', 'Imran', 'Jess']
LAST_NAMES = ['Smith', 'Jones', 'Patel', 'Brown', 'Taylor', 'Khan', 'Wilson', 'Evans', 'Walker', 'Clarke']
JOB_TITLES = ['Facilities Manager', 'Helpdesk Coordinator', 'Maintenance Engineer', 'Site Manager', None]

# (description, work required, statutory compliance)
PLANNED_JOB_SCHEDULES = [
    ('Annual boiler service', 'Full annual service and gas safety check on all boilers.', True),
    ('Quarterly AHU filter change', 'Replace filters and inspect belts on air handling units.', False),
    ('Monthly emergency lighting test', 'Function test of all emergency lighting, log results.', True),
    ('Weekly fire alarm test', 'Activate a call point and confirm the alarm sounds throughout.', True),
    ('Six-monthly lift inspection', 'LOLER thorough examination of passenger lifts.', True),
    ('Legionella temperature checks', 'Monthly sentinel outlet temperature monitoring.', True),
    ('Annual fire door inspection', 'Inspect all fire doors, closers and seals.', True),
    ('Generator load test', 'Run generator under load for one hour and record readings.', False),
]
# (description, work required)
JOBS = [
    ('Boiler not firing', 'Boiler showing fault code and not producing heating or hot water.'),
    ('Leak under kitchen sink', 'Water pooling under the sink unit, needs urgent attention.'),
    ('Lift stuck between floors', 'Lift stopped between floors, no one trapped.'),
    ('Fire alarm fault light on', 'Panel showing a fault on zone 3.'),
    ('Replace failed light fittings', 'Several fittings out in the corridor.'),
    ('No hot water', 'Hot water not reaching temperature in the washrooms.'),
    ('Door closer broken', 'Fire door not closing fully on its own.'),
    ('Air conditioning too warm', 'Meeting rooms not cooling below 25 degrees.'),
    ('Blocked drain', 'Drain outside the rear entrance is backing up.'),
    ('Roof leak', 'Water coming through the ceiling tiles after rain.'),
]


def phone():
    return f'0{random.randint(1000, 9999)} {random.randint(100000, 999999)}'


def address():
    lines = [
        random.choice(['', f'Unit {random.randint(1, 20)}']),
        f'{random.randint(1, 200)} {random.choice(STREETS)}',
        random.choice(TOWNS),
        random.choice(COUNTIES),
        f'{random.choice(POST_CODES)} {random.randint(1, 9)}{random.choice("ABDEFGHJ")}{random.choice("LNPQRSTU")}',
    ]
    return {'address': '\n'.join(line for line in lines if line)}


def days_from_today(low, high):
    return date.today() + timedelta(days=random.randint(low, high))


def clear():
    db.session.execute(job_assets.delete())
    db.session.execute(job_helpers.delete())
    db.session.execute(planned_job_schedule_assets.delete())
    for model in (Notification, Job, Asset, PlannedJobSchedule, Supplier, Location, *REFERENCE_DATA, OTP, UserSession, Person, ImportEvent, User, UserGroupPermission, UserGroup, Organisation):
        db.session.execute(db.delete(model))
    db.session.commit()


def seed(count):
    # verified, as if whoever registered it had entered their first code
    organisation = Organisation(name=f'{random.choice(PLACE_NAMES)} Facilities Ltd', verified_at=datetime.now(timezone.utc))
    admin_group, team_group = create_default_groups(organisation)

    # people who can log in to other organisations, to add to this one too
    users = {user.email: user for user in User.query.all()}
    shared = [user for user in users.values() if user.signin_people()]
    shared = random.sample(shared, min(2, len(shared)))

    # every person has a user, but only some can log in. An email already seeded in another
    # organisation gets the same user, like adding them on the person form
    people = []
    used = set()
    for i in range(10):
        first_name, last_name = random.choice(FIRST_NAMES), random.choice(LAST_NAMES)
        email = f'{first_name}.{last_name}{i + 1}@example.com'.lower()
        if email in used or email in {user.email for user in shared}:
            continue
        used.add(email)
        people.append(Person(
            first_name=first_name,
            last_name=last_name,
            email=email,
            job_title=random.choice(JOB_TITLES),
            mobile=random.choice([None, f'07{random.randint(100, 999)} {random.randint(100000, 999999)}']),
            can_login=i < 5,
            user_group=admin_group if i == 0 else team_group,
            organisation=organisation,
            user=users.get(email) or User(email=email),
        ))
    # the first person is the admin, as if they'd registered it
    organisation.terms_accepted_at = organisation.verified_at
    organisation.terms_accepted_by_name = people[0].full_name
    organisation.terms_accepted_by_email = people[0].email
    for user in shared:
        existing = user.signin_people()[0]
        people.append(Person(
            first_name=existing.first_name,
            last_name=existing.last_name,
            email=user.email,
            can_login=True,
            user_group=team_group,
            organisation=organisation,
            user=user,
        ))

    reference_data = {
        model: [model(description=description, organisation=organisation) for description in descriptions]
        for model, descriptions in REFERENCE_DATA.items()
    }

    # one of the organisation's items of a kind, or sometimes none
    def pick(model, none_chance=0.2):
        return None if random.random() < none_chance else random.choice(reference_data[model])

    # a condition and the date it was set, for an asset
    def condition(model):
        item = pick(model, 0.3)
        return dict(condition_set=days_from_today(-730, 0) if item else None, **{f'{model.__tablename__}': item})

    # top-level locations (sites) with one to three buildings in each, and one to four zones in each
    # building, so paths are three deep at most. Site names are unique; past 100 sites they get a number
    names = random.sample([f'{place} {kind}' for place in PLACE_NAMES for kind in SITE_TYPES], min(count, len(PLACE_NAMES) * len(SITE_TYPES)))
    names += [f'{random.choice(PLACE_NAMES)} {random.choice(SITE_TYPES)} {i + 1}' for i in range(len(names), count)]
    # one of the organisation's location categories, by description
    def location_category(description):
        return next(item for item in reference_data[LocationCategory] if item.description == description)

    sites = [
        Location(name=name, materialised_path=name, telephone=phone(), location_category=location_category('Site'), organisation=organisation, **address())
        for name in names
    ]
    # most sites have a main contact
    for site in sites:
        if random.random() < 0.7:
            site.main_contact = random.choice(people)
    locations = list(sites)
    # every location's site, and the locations at or inside each site
    site_of = {}
    in_site = {}
    for site in sites:
        site_of[site] = site
        in_site[site] = [site]
        for building_name in random.sample(BUILDING_NAMES, random.randint(1, 3)):
            building = Location(name=building_name, parent=site, materialised_path=build_path(site, building_name), location_category=location_category('Building'), organisation=organisation)
            locations.append(building)
            site_of[building] = site
            in_site[site].append(building)
            for zone_name in random.sample(ZONE_NAMES, random.randint(1, 4)):
                zone = Location(name=zone_name, parent=building, materialised_path=build_path(building, zone_name),
                                location_category=location_category('Floor' if zone_name in ('Basement', 'Roof') or zone_name.endswith('Floor') else 'Room'), organisation=organisation)
                locations.append(zone)
                site_of[zone] = site
                in_site[site].append(zone)

    # most records go to a handful of busy sites, so the related tabs have something to show. Within the
    # site, a record is at the site itself, a building or a zone
    busy_sites = sites[:max(1, count // 5)]

    def pick_location():
        site = random.choice(busy_sites) if random.random() < 0.8 else random.choice(sites)
        return random.choice(in_site[site])

    suppliers = [
        Supplier(
            name=f'{random.choice(SUPPLIER_PREFIXES)} {random.choice(SUPPLIER_TRADES)} {random.choice(SUPPLIER_SUFFIXES)}',
            telephone=phone(),
            supplier_category=pick(SupplierCategory),
            organisation=organisation,
            **address()
        )
        for _ in range(count)
    ]

    assets = []
    # serial numbers are unique per organisation
    serials = random.sample(range(10000000, 100000000), count)
    for i in range(count):
        description, makes = random.choice(ASSET_TYPES)
        purchased = days_from_today(-3650, -30)
        assets.append(Asset(
            description=f'{description} - {random.choice(LOCATIONS)}',
            asset_number=f'AST-{i + 1:04d}',
            make=random.choice(makes),
            model=f'{random.choice("ABCDEFGHKLMPRSTX")}{random.randint(100, 999)}',
            serial_number=f'SN{serials[i]}',
            purchase_date=purchased,
            purchased_from=random.choice(['', random.choice(suppliers).name]) or None,
            warranty_expiry=purchased + timedelta(days=365 * random.randint(1, 5)),
            estimated_replacement_date=purchased + timedelta(days=365 * random.randint(10, 25)),
            estimated_replacement_cost=Decimal(random.randint(5000, 2500000)) / 100,
            location=pick_location(),
            asset_category=pick(AssetCategory),
            **condition(AssetCondition),
            organisation=organisation,
        ))

    planned_job_schedules = []
    for _ in range(count):
        description, work_required, statutory_compliance = random.choice(PLANNED_JOB_SCHEDULES)
        next_due_by = days_from_today(1, 365)
        # notice shorter than the gap between jobs
        frequency = random.choice(FREQUENCIES)[0]
        notice_days = {'daily': 0, 'weekly': random.randint(0, 3), 'two_weekly': random.randint(0, 7)}.get(frequency, random.choice([7, 14, 21, 28]))
        planned_job_schedules.append(PlannedJobSchedule(
            description=description,
            work_required=work_required,
            next_due_by=next_due_by,
            frequency=frequency,
            notice_days=notice_days,
            next_job_creation_date=job_creation_date(next_due_by, notice_days),
            statutory_compliance=statutory_compliance,
            is_active=random.random() > 0.1,
            location=pick_location(),
            job_category=pick(JobCategory),
            estimated_cost=random.choice([None, Decimal(random.randint(50, 5000))]),
            estimated_duration=random.choice([None, Decimal('0.5'), Decimal('1'), Decimal('2')]),
            organisation=organisation,
        ))

    jobs = []
    for _ in range(count):
        # roughly a third of jobs are raised from a planned job schedule
        # inactive schedules raise nothing, so only active ones have jobs
        if random.random() < 0.33:
            planned_job_schedule = random.choice([planned_job_schedule for planned_job_schedule in planned_job_schedules if planned_job_schedule.is_active])
            # rejecting a planned job moves its schedule on, so seeded ones aren't rejected
            status = random.choice([key for key, _ in JOB_STATUSES if key != 'rejected'])
            # a schedule has at most one job that isn't closed, and waits on it like raise_job() leaves it
            if planned_job_schedule.next_job_creation_date is None:
                status = 'closed'
            elif status != 'closed':
                planned_job_schedule.next_job_creation_date = None
            jobs.append(Job(
                description=planned_job_schedule.description,
                work_required=planned_job_schedule.work_required,
                due_by=days_from_today(-30, 90),
                statutory_compliance=planned_job_schedule.statutory_compliance,
                status=status,
                source='planned',
                location=planned_job_schedule.location,
                job_category=planned_job_schedule.job_category,
                estimated_cost=planned_job_schedule.estimated_cost,
                organisation=organisation,
                planned_job_schedule=planned_job_schedule,
            ))
        else:
            description, work_required = random.choice(JOBS)
            status = random.choice(JOB_STATUSES)[0]
            jobs.append(Job(
                description=description,
                work_required=work_required,
                due_by=random.choice([None, days_from_today(-30, 60)]),
                status=status,
                rejection_reason='Not our responsibility: the landlord maintains this.' if status == 'rejected' else None,
                source='reactive',
                location=pick_location(),
                job_category=pick(JobCategory),
                organisation=organisation,
            ))

    # what the app sets as a job moves on: who it's assigned to (a supplier or a person, plus some helpers)
    # and the times
    now = datetime.now(timezone.utc)
    for job in jobs:
        if job.status == 'closed':
            job.closed_at = days_from_today(-30, 0)
        if job.status not in ('new', 'rejected'):
            job.approved_at = now - timedelta(days=40)
        if job.status in ASSIGNED_JOB_STATUSES or job.status == 'closed':
            job.issued_at = now - timedelta(days=38)
            if job.status != 'pending_acceptance':
                job.assigned_at = job.issued_at
            job.assigned_by = random.choice(people)
            if random.random() < 0.5:
                job.assigned_supplier = random.choice(suppliers)
            else:
                job.assigned_person = random.choice(people)
            job.helpers = random.sample([person for person in people if person is not job.assigned_person], random.randint(0, 2))
        # the steps it was assigned with, as far as it's got. started_at is only there if someone pressed Start
        if job.status in ('pending_acceptance', 'pending_scheduling', 'in_progress'):
            job.require_start_confirmation = random.random() < 0.5
        if job.status in ('completed', 'closed'):
            job.require_start_confirmation = True
        # at pending_start, either they must confirm the start or it's waiting for its date
        if job.status == 'pending_start':
            job.require_start_confirmation = random.random() < 0.5
        if job.status == 'pending_scheduling' or (job.status == 'pending_start' and not job.require_start_confirmation) or (job.status in ('pending_acceptance', 'pending_start') and random.random() < 0.5):
            job.require_scheduling = True
        if job.require_scheduling and job.status == 'pending_start':
            soon = days_from_today(1, 20)
            job.scheduled_for = job.due_by if job.due_by and days_from_today(0, 0) <= job.due_by < soon else soon
        if job.require_start_confirmation and job.status in ('in_progress', 'completed', 'closed'):
            job.started_at = now - timedelta(days=35)
        if job.status in ('completed', 'closed'):
            job.completed_at = now - timedelta(days=31)
            job.completion_comments = 'Work carried out as required. Area left clean and tidy.'
        if job.status == 'rejected':
            job.rejected_at = now - timedelta(days=40)

    # a few reactive jobs are follow-up work for a completed or closed job at the same site. A follow-up isn't
    # a parent as well, or two jobs could end up as each other's parents, which can't be saved
    follow_ups = [job for job in jobs if job.source == 'reactive'][:max(1, count // 10)]
    finished = [job for job in jobs if job.status in ('completed', 'closed') and job not in follow_ups]
    for job in follow_ups:
        parents = [parent for parent in finished if site_of[parent.location] is site_of[job.location]]
        if parents:
            job.source = 'follow_up'
            job.parent_job = random.choice(parents)
            job.location = job.parent_job.location

    # link each asset to some jobs and planned job schedules at its own site
    for asset in assets:
        site_jobs = [job for job in jobs if site_of[job.location] is site_of[asset.location]]
        site_planned_job_schedules = [planned_job_schedule for planned_job_schedule in planned_job_schedules if site_of[planned_job_schedule.location] is site_of[asset.location]]
        asset.jobs = random.sample(site_jobs, min(len(site_jobs), random.randint(0, 3)))
        asset.planned_job_schedules = random.sample(site_planned_job_schedules, min(len(site_planned_job_schedules), random.randint(0, 2)))

    db.session.add_all([organisation] + [item for items in reference_data.values() for item in items] + people + locations + suppliers + assets + planned_job_schedules + jobs)
    db.session.commit()

    links = sum(len(asset.jobs) + len(asset.planned_job_schedules) for asset in assets)
    print(f"Created organisation '{organisation.name}' with {len(people)} people ({sum(1 for person in people if person.can_login)} can log in).")
    for user in shared:
        print(f'{user.email} is in another organisation too, so can switch between them.')
    print(f'Created {count} each of top-level locations, suppliers, assets, planned job schedules and jobs, plus {links} asset links.')
    print(f'Created {len(locations) - count} locations inside them (buildings and zones).')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Populate the database with random test data.')
    parser.add_argument('--count', type=int, default=50, help='number of each record type to create (default 50)')
    parser.add_argument('--clear', action='store_true', help='delete all existing data before seeding')
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        if args.clear:
            clear()
            print('Cleared existing data.')
        seed(args.count)
