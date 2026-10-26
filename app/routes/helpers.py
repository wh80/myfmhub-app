import uuid
from datetime import date
from functools import wraps
from flask import request, g, redirect, url_for, flash, abort
from app import db
from app.models import Site, Building, Zone
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


def require_login():
    """Send anyone without a valid session to the login page, remembering where they were going.

    Used as a blueprint before_request hook, and by login_required."""
    if g.user is None:
        flash('Please sign in to continue.', 'warning')
        next_url = request.full_path.rstrip('?') if request.method == 'GET' else None
        return redirect(url_for('auth.login', next=next_url))


def require_platform_admin():
    """before_request hook for /admin. Anyone who isn't a platform admin gets a 404, so the area stays hidden."""
    if g.user is None:
        return require_login()
    if not g.user.is_platform_admin:
        abort(404)


def require_permission():
    """before_request hook, registered after require_login. Looks up the permission the endpoint needs in
    ENDPOINT_PERMISSIONS and refuses (403) if the user's group doesn't have it, or if the endpoint isn't listed."""
    permission = ENDPOINT_PERMISSIONS.get(request.endpoint)
    if permission is None or not can(g.user, permission):
        abort(403)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        return require_login() or view(*args, **kwargs)
    return wrapped


def org_query(model):
    """Query for a model, limited to the signed-in user's organisation."""
    return model.query.filter_by(organisation_id=g.user.organisation_id)


def org_get_or_404(model, id):
    """Fetch one record from the signed-in user's organisation, or 404 (including when it belongs to another organisation)."""
    return org_query(model).filter_by(id=id).first_or_404()


def name_taken(query, column, name, record=None):
    """True if a record in query already has this name, ignoring case. record is the one being edited."""
    query = query.filter(db.func.lower(column) == name.strip().lower())
    if record is not None:
        query = query.filter(type(record).id != record.id)
    return query.first() is not None


### LOCATION ###
# assets, jobs and job schedules have a required site and an optional building and zone. The selects
# are dependent: every building and zone is listed with its parent's id as data-parent, and app.js
# shows only the buildings at the chosen site and the zones in the chosen building

def set_location_choices(form):
    """Fill a form's site, building and zone selects with the organisation's records."""
    form.site_id.choices = [('', '--- Select Site ---')] + [
        (str(site.id), site.name) for site in org_query(Site).order_by(Site.name)
    ]
    form.building_id.choices = [('', '--- No building ---')] + [
        (str(building.id), building.name, {'data-parent': str(building.site_id)})
        for building in org_query(Building).order_by(Building.name)
    ]
    form.zone_id.choices = [('', '--- No zone ---')] + [
        (str(zone.id), zone.name, {'data-parent': str(zone.building_id)})
        for zone in org_query(Zone).order_by(Zone.name)
    ]


def check_location(form):
    """The building has to be at the chosen site, and the zone in the chosen building. The selects only
    offer the organisation's records, but any of them can be posted. Adds errors to the form."""
    building_id, zone_id = location_ids(form)
    building = org_query(Building).filter_by(id=building_id).first() if building_id else None
    zone = org_query(Zone).filter_by(id=zone_id).first() if zone_id else None
    ok = True
    if building and str(building.site_id) != str(form.site_id.data):
        form.building_id.errors.append("This building isn't at the chosen site.")
        ok = False
    if zone and (building is None or zone.building_id != building.id):
        form.zone_id.errors.append("This zone isn't in the chosen building.")
        ok = False
    return ok


def location_ids(form):
    """The form's building and zone as UUIDs, or None."""
    return parse_uuid(form.building_id.data), parse_uuid(form.zone_id.data)


def location_options():
    """The sites, buildings and zones for an index page's location filters."""
    return dict(
        sites=org_query(Site).order_by(Site.name).all(),
        buildings=org_query(Building).order_by(Building.name).all(),
        zones=org_query(Zone).order_by(Zone.name).all(),
    )


def filter_location(query, model):
    """Apply ?building_id= and ?zone_id= to an index query. Returns the query and the values, for the template."""
    building_id = parse_uuid(request.args.get('building_id'))
    zone_id = parse_uuid(request.args.get('zone_id'))
    if building_id:
        query = query.filter(model.building_id == building_id)
    if zone_id:
        query = query.filter(model.zone_id == zone_id)
    return query, dict(building_id=building_id, zone_id=zone_id)


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
