from flask import Blueprint, render_template, redirect, url_for, request, flash, g
from app import db
from app.forms import LocationForm
from app.models import Location, Asset, Job, JobSchedule, LocationCategory, Person
from app.routes.helpers import require_login, require_permission, org_query, org_get_or_404, parse_uuid, files_tab, related_page, org_locations, in_location, set_reference_choices, reference_items, filter_reference
from app.services.addresses import clean_address
from app.services.exports import export_response
from app.services.locations import descendants, location_tree, path_problem, set_path
locations_bp = Blueprint('locations', __name__, url_prefix="/locations")
locations_bp.before_request(require_login)
locations_bp.before_request(require_permission)

# locations nest to any depth: a site, a building in it, a floor in that... Each has a materialised_path
# of the names from the top down ('Head Office > Main Building'), unique per organisation ignoring case,
# kept up to date by set_path (services/locations.py). Deleting a location deletes the locations in it
# and the assets, jobs and job schedules at all of them

# the index table's columns (the search and import results list), for downloads. Keys match data-col in
# locations/index.html
LOCATION_COLUMNS = [
    ('path', 'Location', lambda location: location.materialised_path),
    ('name', 'Name', lambda location: location.name),
    ('category', 'Location Category', lambda location: location.location_category.description if location.location_category else None),
    ('telephone', 'Telephone', lambda location: location.telephone),
]

# the index filters, shared by the page and its downloads
def filtered_locations():
    search = request.args.get('search', '').strip()

    query = org_query(Location)

    # set by "View imported items" on an import
    import_id = parse_uuid(request.args.get('import_id'))
    if import_id:
        query = query.filter(Location.import_event_id == import_id)

    if search:
        query = query.filter(Location.materialised_path.ilike(f'%{search}%'))

    # e.g. every Building
    query, location_category_id = filter_reference(query, Location, 'location_category_id')

    return query.order_by(db.func.lower(Location.materialised_path), Location.id), dict(
        search=search, import_id=import_id, location_category_id=location_category_id)

# with no search or filter it shows every location as a tree, otherwise the matching ones as a list
@locations_bp.route('/')
def index():
    query, filters = filtered_locations()
    categories = reference_items(LocationCategory)
    if filters['search'] or filters['import_id'] or filters['location_category_id']:
        page = request.args.get('page', 1, type=int)
        pagination = query.paginate(page=page, per_page=25, error_out=False)
        return render_template('locations/index.html', locations=pagination.items, pagination=pagination, tree=None,
                               location_categories=categories, **filters)

    return render_template('locations/index.html', tree=location_tree(query.all()), pagination=None,
                           location_categories=categories, **filters)

@locations_bp.route('/export/<any(csv, pdf):file_format>')
def export(file_format):
    query, _ = filtered_locations()
    return export_response(file_format, 'locations', 'Locations', LOCATION_COLUMNS, query.all())

def set_parent_choices(form, location=None):
    """Every location in the organisation, by path, except (when editing) the location itself and the
    ones inside it, which it can't move into. WTForms refuses anything else."""
    excluded = {location.id, *(child.id for child in descendants(location))} if location else set()
    form.parent_id.choices = [('', '--- None (top-level location) ---')] + [
        (str(item.id), item.materialised_path) for item in org_locations() if item.id not in excluded
    ]

# everyone in the organisation, so WTForms refuses anyone else
def set_main_contact_choices(form):
    people = org_query(Person).order_by(Person.first_name, Person.last_name, Person.id).all()
    form.main_contact_id.choices = [('', '-')] + [(str(person.id), f'{person.full_name} ({person.email})') for person in people]

def chosen_parent(form):
    parent_id = parse_uuid(form.parent_id.data)
    return org_query(Location).filter_by(id=parent_id).first() if parent_id else None

def check_path(form, parent, location=None):
    """Refuse a path that's already used (ignoring case), a name with '>', or a move inside itself."""
    problem = path_problem(g.person.organisation_id, form.name.data, parent, location)
    if problem:
        field, message = problem
        form[field].errors.append(message)
        return False
    return True

# ?parent_id= starts it inside that location (the Add Sub-Location button)
@locations_bp.route('/create', methods=['GET', 'POST'])
def create():
    form = LocationForm()
    set_parent_choices(form)
    set_reference_choices(form)
    set_main_contact_choices(form)
    if request.method == 'GET' and request.args.get('parent_id'):
        form.parent_id.data = str(org_get_or_404(Location, parse_uuid(request.args['parent_id'])).id)

    if form.validate_on_submit():
        parent = chosen_parent(form)
        if check_path(form, parent):
            location = Location(
                name=form.name.data,
                parent=parent,
                address=clean_address(form.address.data),
                telephone=form.telephone.data or None,
                location_category_id=parse_uuid(form.location_category_id.data),
                main_contact_id=parse_uuid(form.main_contact_id.data),
                organisation_id=g.person.organisation_id,
            )
            set_path(location)
            db.session.add(location)
            db.session.commit()
            flash(f"Location '{location.materialised_path}' created successfully!", 'success')
            return redirect(url_for('locations.index'))

    return render_template('locations/create.html', form=form)

@locations_bp.route('/<uuid:id>', methods=['GET'])
def view(id):
    location = org_get_or_404(Location, id)
    return render_template('locations/view/overview.html', location=location, active_tab="overview")

@locations_bp.route('/<uuid:id>/edit', methods=['GET', 'POST'])
def edit(id):
    location = org_get_or_404(Location, id)

    form = LocationForm(obj=location)
    set_parent_choices(form, location)
    set_reference_choices(form)
    set_main_contact_choices(form)

    if form.validate_on_submit():
        parent = chosen_parent(form)
        if check_path(form, parent, location):
            location.name = form.name.data
            location.parent = parent
            location.address = clean_address(form.address.data)
            location.telephone = form.telephone.data or None
            location.location_category_id = parse_uuid(form.location_category_id.data)
            location.main_contact_id = parse_uuid(form.main_contact_id.data)
            # renaming or moving it changes its path, and the paths of every location inside it
            set_path(location)
            db.session.commit()
            flash(f"Location '{location.materialised_path}' updated successfully!", 'success')
            return redirect(url_for('locations.view', id=location.id))

    return render_template('locations/edit.html', location=location, form=form)

@locations_bp.route('/<uuid:id>/delete', methods=['GET', 'POST'])
def delete(id):
    location = org_get_or_404(Location, id)

    if request.method == "POST":
        path = location.materialised_path
        # cascades to the locations inside it, and the assets, jobs and job schedules at all of them
        db.session.delete(location)
        db.session.commit()
        flash(f"Location '{path}' deleted, with everything in it.", 'success')
        return redirect(url_for('locations.index'))

    counts = dict(
        sub_locations=len(descendants(location)),
        assets=in_location(Asset, location).count(),
        jobs=in_location(Job, location).count(),
        job_schedules=in_location(JobSchedule, location).count(),
    )
    return render_template('locations/delete.html', location=location, counts=counts)

### RELATED DATA ###
# the tabs list the records at this location and at every location inside it

@locations_bp.route("/<uuid:id>/assets")
def assets(id):
    location = org_get_or_404(Location, id)
    pagination, search = related_page(in_location(Asset, location), Asset)
    return render_template("locations/view/assets.html", location=location, assets=pagination.items, pagination=pagination, search=search, active_tab="assets")

@locations_bp.route("/<uuid:id>/jobs")
def jobs(id):
    location = org_get_or_404(Location, id)
    pagination, search = related_page(in_location(Job, location), Job)
    return render_template("locations/view/jobs.html", location=location, jobs=pagination.items, pagination=pagination, search=search, active_tab="jobs")

@locations_bp.route("/<uuid:id>/job-schedules")
def job_schedules(id):
    location = org_get_or_404(Location, id)
    pagination, search = related_page(in_location(JobSchedule, location), JobSchedule)
    return render_template("locations/view/job_schedules.html", location=location, job_schedules=pagination.items, pagination=pagination, search=search, active_tab="job_schedules")

@locations_bp.route("/<uuid:id>/files")
def files(id):
    return files_tab(Location, id, "locations/view/files.html", "location")
