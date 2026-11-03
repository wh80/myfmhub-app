import uuid
from datetime import date
from functools import wraps
from flask import request, g, redirect, url_for, flash, abort, render_template
from app import db
from sqlalchemy.orm import with_parent
from app.models import Location, Asset, Job, JobSchedule, File, JobCategory, AssetCategory, AssetCondition, LocationCategory, SupplierCategory, FileCategory
from app.services.locations import SEPARATOR, subtree_ids
from app.services.auth import revoke_session, clear_session_cookie
from app.services.permissions import ENDPOINT_PERMISSIONS, can


def selected_ids(field):
    """Return the set of valid UUIDs submitted under a checkbox field, skipping junk values."""
    ids = set()
    for value in request.form.getlist(field):
        try:
            ids.add(uuid.UUID(value))
        except ValueError:
            pass
    return ids


def parse_uuid(value):
    """Return value as a UUID, or None if it isn't one (for ids in query strings)."""
    try:
        return uuid.UUID(value)
    except (TypeError, ValueError):
        return None


def parse_date(value):
    """Return a YYYY-MM-DD string as a date, or None if it isn't one (for dates in query strings)."""
    try:
        return date.fromisoformat(value.strip())
    except (AttributeError, ValueError):
        return None


def safe_next_url(url):
    """Return url if it's a path on this site, otherwise None, so ?next= can't redirect off-site."""
    if url and url.startswith('/') and not url.startswith('//') and '\\' not in url:
        return url
    return None


def require_user():
    """Send anyone without a valid session to the login page, remembering where they were going.
    For the few pages that don't need an organisation, like Switch organisation."""
    if g.user is None:
        flash('Please sign in to continue.', 'warning')
        next_url = request.full_path.rstrip('?') if request.method == 'GET' else None
        return redirect(url_for('auth.login', next=next_url))


def require_login():
    """Like require_user, and the session must be working in an organisation (g.person): anyone who
    hasn't chosen one yet goes to Switch organisation, and a platform admin with none goes to /admin.

    Used as a blueprint before_request hook, and by login_required."""
    response = require_user()
    if response:
        return response
    if g.person is None:
        if g.user.signin_people():
            next_url = request.full_path.rstrip('?') if request.method == 'GET' else None
            return redirect(url_for('auth.switch_organisation', next=next_url))
        return redirect(url_for('admin.index'))


def require_platform_admin():
    """before_request hook for /admin. Anyone who isn't a platform admin gets a 404, so the area stays hidden."""
    if g.user is None:
        return require_user()
    if not g.user.is_platform_admin:
        abort(404)


def require_permission():
    """before_request hook, registered after require_login. Looks up the permission the endpoint needs in
    ENDPOINT_PERMISSIONS and refuses (403) if the user's group doesn't have it, or if the endpoint isn't listed."""
    permission = ENDPOINT_PERMISSIONS.get(request.endpoint)
    if permission is None or not can(g.person, permission):
        abort(403)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        return require_login() or view(*args, **kwargs)
    return wrapped


def user_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        return require_user() or view(*args, **kwargs)
    return wrapped


def org_query(model):
    """Query for a model, limited to the organisation the session is working in."""
    return model.query.filter_by(organisation_id=g.person.organisation_id)


def org_get_or_404(model, id):
    """Fetch one record from the session's organisation, or 404 (including when it belongs to another organisation)."""
    return org_query(model).filter_by(id=id).first_or_404()


def name_taken(query, column, name, record=None):
    """True if a record in query already has this name, ignoring case. record is the one being edited."""
    query = query.filter(db.func.lower(column) == name.strip().lower())
    if record is not None:
        query = query.filter(type(record).id != record.id)
    return query.first() is not None


### LOCATION ###
# assets, jobs and job schedules are each at one location, chosen from a searchable select of every
# location in the organisation by its path ('Head Office > Main Building > Ground Floor')

def org_locations():
    """The organisation's locations, by path, so each one sorts under the location it's in."""
    return org_query(Location).order_by(db.func.lower(Location.materialised_path), Location.id).all()


def set_location_choices(form):
    """Fill a form's location select. WTForms refuses anything else, including another organisation's."""
    form.location_id.choices = [('', '--- Select Location ---')] + [
        (str(location.id), location.materialised_path) for location in org_locations()
    ]


def location_options():
    """The locations for an index page's location filter."""
    return dict(locations=org_locations())


def filter_location(query, model):
    """Apply ?location_id= to an index query: records at that location or any location inside it.
    Returns the query and the value, for the template."""
    location_id = parse_uuid(request.args.get('location_id'))
    location = org_query(Location).filter_by(id=location_id).first() if location_id else None
    if location:
        query = query.filter(model.location_id.in_(subtree_ids(location)))
    return query, dict(location_id=location.id if location else None)


def create_from(allow_asset=True):
    """The record a create page was opened from, by the Create buttons on an asset's or a location's tabs:
    ?asset_id= (a job or job schedule for that asset, linked to it on save) or ?location_id=. Returns
    (asset, location): the new record is fixed at that location, so the form's location select is hidden
    and the route overrides whatever is posted for it. Linking needs edit on the asset, like its link pages."""
    if allow_asset and request.args.get('asset_id'):
        asset = org_get_or_404(Asset, parse_uuid(request.args['asset_id']))
        if not can(g.person, 'assets.edit'):
            abort(403)
        return asset, asset.location
    if request.args.get('location_id'):
        return None, org_get_or_404(Location, parse_uuid(request.args['location_id']))
    return None, None


def in_location(model, location):
    """A query for the records at the location or any location inside it, for its tabs."""
    return org_query(model).filter(model.location_id.in_(subtree_ids(location)))


def same_top_location(model, location):
    """A condition for records whose location is in the same top-level location as this one, for link
    pages: a job can be linked to assets anywhere at its site."""
    top = location.materialised_path.split(SEPARATOR)[0]
    return model.location.has(db.or_(
        Location.materialised_path == top,
        Location.materialised_path.startswith(top + SEPARATOR, autoescape=True),
    ))


### SESSION AND USER ACTIONS ###
# shared by the organisation sessions page and /admin. The forms post a hidden `next` field so the
# redirect goes back to the same page, filters and all

def back_to(fallback_url):
    return redirect(safe_next_url(request.form.get('next')) or fallback_url)


def revoke_and_redirect(user_session, fallback_url):
    """Revoke a session. Revoking the session you're using signs you out."""
    revoke_session(user_session)

    if user_session.id == g.user_session.id:
        flash("You've been signed out.", 'success')
        return clear_session_cookie(redirect(url_for('auth.login')))

    flash('Session revoked.', 'success')
    return back_to(fallback_url)


def signed_out_or_back(message, fallback_url):
    """After revoking several sessions at once: if ours was among them we've been signed out, otherwise go back."""
    db.session.refresh(g.user_session)

    if g.user_session.revoked_at:
        flash(f'{message} That included your session, so you have been signed out.', 'success')
        return clear_session_cookie(redirect(url_for('auth.login')))

    flash(message, 'success')
    return back_to(fallback_url)


def files_tab(model, id, template, name):
    """A record's Files tab (templates/files/_files_tab.html). require_permission checks view on the
    record; the files themselves need files.view too."""
    if not can(g.person, 'files.view'):
        abort(403)
    record = org_get_or_404(model, id)
    pagination, search = related_page(related(record, model.files, File), File)
    return render_template(template, active_tab='files', files=pagination.items, pagination=pagination, search=search, **{name: record})


### RELATED TABS AND LINK PAGES ###
# the columns ?search= looks in, and the sort order, for each kind of record they list

RELATED_LISTS = {
    Asset: ([Asset.description, Asset.asset_number, Asset.make, Asset.model, Asset.serial_number], Asset.description),
    Job: ([Job.description, Job.work_required], Job.description),
    JobSchedule: ([JobSchedule.description, JobSchedule.work_required], JobSchedule.description),
    File: ([File.description, File.filename], File.description),
}


def related(record, relationship, model):
    """A query for the records in one of record's relationships, e.g. related(asset, Asset.jobs, Job)."""
    return org_query(model).filter(with_parent(record, relationship))


def related_page(query, model):
    """Search (?search=) and paginate (?page=) a related tab's or link page's query, like an index page.
    Returns the pagination and the search text, for _related_search.html and pagination_nav."""
    columns, sort = RELATED_LISTS[model]
    search = request.args.get('search', '').strip()
    if search:
        query = query.filter(db.or_(*(column.ilike(f'%{search}%') for column in columns)))
    page = request.args.get('page', 1, type=int)
    return query.order_by(sort, model.id).paginate(page=page, per_page=25, error_out=False), search


### CATEGORIES AND CONDITIONS ###
# the reference data (services/reference_data.py) behind each form field and index filter of that name

REFERENCE_FIELDS = {
    'job_category_id': JobCategory,
    'asset_category_id': AssetCategory,
    'asset_condition_id': AssetCondition,
    'location_category_id': LocationCategory,
    'supplier_category_id': SupplierCategory,
    'file_category_id': FileCategory,
}


def reference_items(model):
    """The organisation's categories or conditions of one kind, for selects and filters."""
    return org_query(model).order_by(model.description, model.id).all()


def set_reference_choices(form):
    """Fill in any category and condition selects on the form: '-' for none, then the organisation's
    items, so WTForms refuses anything else. Store the value with parse_uuid."""
    for name, model in REFERENCE_FIELDS.items():
        if name in form:
            form[name].choices = [('', '-')] + [(str(item.id), item.description) for item in reference_items(model)]


def filter_reference(query, model, name):
    """Apply an index's ?<name>=<id> category or condition filter. Returns the query and the id as a
    string ('' when unset), for the filter select."""
    value = parse_uuid(request.args.get(name))
    if value:
        query = query.filter(getattr(model, name) == value)
    return query, str(value) if value else ''
