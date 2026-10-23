import uuid
from datetime import date
from functools import wraps
from flask import request, g, redirect, url_for, flash, abort
from app import db
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
