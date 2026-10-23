from flask import Blueprint, render_template, redirect, url_for, request, flash, g
from app import db
from app.models import Organisation, User, UserSession
from app.routes.helpers import require_platform_admin, parse_uuid, back_to, revoke_and_redirect, signed_out_or_back
from app.services.auth import (
    active_sessions, active_session_counts, revoke_user_sessions, revoke_organisation_sessions, revoke_all_sessions,
    suspend_organisation, reinstate_organisation,
)
admin_bp = Blueprint('admin', __name__, url_prefix="/admin")
admin_bp.before_request(require_platform_admin)

@admin_bp.route('/')
def index():
    return redirect(url_for('admin.organisations'))

### ORGANISATIONS ###

@admin_bp.route('/organisations')
def organisations():
    search = request.args.get('search', '').strip()
    page = request.args.get('page', 1, type=int)

    query = Organisation.query

    if search:
        query = query.filter(Organisation.name.ilike(f'%{search}%'))

    pagination = query.order_by(Organisation.name, Organisation.id).paginate(
        page=page, per_page=25, error_out=False
    )

    ids = [organisation.id for organisation in pagination.items]
    user_counts = dict(
        db.session.query(User.organisation_id, db.func.count(User.id))
        .filter(User.organisation_id.in_(ids))
        .group_by(User.organisation_id)
        .all()
    ) if ids else {}
    session_counts = dict(
        active_sessions().join(User)
        .filter(User.organisation_id.in_(ids))
        .with_entities(User.organisation_id, db.func.count(UserSession.id))
        .group_by(User.organisation_id)
        .all()
    ) if ids else {}

    return render_template(
        'admin/organisations.html',
        organisations=pagination.items,
        pagination=pagination,
        user_counts=user_counts,
        session_counts=session_counts,
        search=search,
    )

@admin_bp.route('/organisations/<uuid:id>/suspend', methods=['POST'])
def suspend_organisation_route(id):
    organisation = Organisation.query.get_or_404(id)

    if organisation.id == g.user.organisation_id:
        flash("You can't suspend your own organisation.", 'warning')
    elif organisation.suspended_at:
        flash(f"'{organisation.name}' is already suspended.", 'warning')
    else:
        count = suspend_organisation(organisation)
        flash(f"Suspended '{organisation.name}' and revoked {count} session(s).", 'success')

    return back_to(url_for('admin.organisations'))

@admin_bp.route('/organisations/<uuid:id>/reinstate', methods=['POST'])
def reinstate_organisation_route(id):
    organisation = Organisation.query.get_or_404(id)
    reinstate_organisation(organisation)
    flash(f"Reinstated '{organisation.name}'. Its users can sign in again.", 'success')
    return back_to(url_for('admin.organisations'))

@admin_bp.route('/organisations/<uuid:id>/revoke-sessions', methods=['POST'])
def revoke_organisation(id):
    organisation = Organisation.query.get_or_404(id)
    count = revoke_organisation_sessions(organisation)
    return signed_out_or_back(f"Revoked {count} session(s) for '{organisation.name}'.", url_for('admin.sessions'))

### SESSIONS ###

@admin_bp.route('/sessions')
def sessions():
    search = request.args.get('search', '').strip()
    organisation_id = request.args.get('organisation_id', '').strip()
    page = request.args.get('page', 1, type=int)

    query = active_sessions().join(User)

    if search:
        query = query.filter(User.email.ilike(f'%{search}%'))

    if parse_uuid(organisation_id):
        query = query.filter(User.organisation_id == parse_uuid(organisation_id))

    pagination = query.order_by(UserSession.last_seen_at.desc(), UserSession.id).paginate(
        page=page, per_page=25, error_out=False
    )

    return render_template(
        'admin/sessions.html',
        user_sessions=pagination.items,
        pagination=pagination,
        organisations=Organisation.query.order_by(Organisation.name, Organisation.id).all(),
        search=search,
        organisation_id=organisation_id,
    )

@admin_bp.route('/sessions/<uuid:id>/revoke', methods=['POST'])
def revoke(id):
    user_session = active_sessions().filter(UserSession.id == id).first_or_404()
    return revoke_and_redirect(user_session, url_for('admin.sessions'))

@admin_bp.route('/sessions/revoke-all', methods=['POST'])
def revoke_all():
    count = revoke_all_sessions()
    return signed_out_or_back(f'Revoked all {count} session(s). Everyone will need to sign in again.', url_for('admin.sessions'))

### USERS ###

@admin_bp.route('/users')
def users():
    search = request.args.get('search', '').strip()
    organisation_id = request.args.get('organisation_id', '').strip()
    page = request.args.get('page', 1, type=int)

    query = User.query

    if search:
        query = query.filter(User.email.ilike(f'%{search}%'))

    if parse_uuid(organisation_id):
        query = query.filter(User.organisation_id == parse_uuid(organisation_id))

    pagination = query.order_by(User.email, User.id).paginate(
        page=page, per_page=25, error_out=False
    )

    return render_template(
        'admin/users.html',
        users=pagination.items,
        pagination=pagination,
        session_counts=active_session_counts(pagination.items),
        organisations=Organisation.query.order_by(Organisation.name, Organisation.id).all(),
        search=search,
        organisation_id=organisation_id,
    )

@admin_bp.route('/users/<uuid:id>/revoke-sessions', methods=['POST'])
def revoke_user(id):
    user = User.query.get_or_404(id)
    count = revoke_user_sessions(user)
    return signed_out_or_back(f'Revoked {count} session(s) for {user.email}.', url_for('admin.users'))
