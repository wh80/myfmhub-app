from flask import Blueprint, render_template, redirect, url_for, request, flash, g
from app import db
from app.forms import SupportTicketNoteForm
from app.models import Organisation, Person, User, UserSession, SupportTicket, SupportTicketNote, SUPPORT_TICKET_STATUSES, SUPPORT_TICKET_STATUS_LABELS
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
        db.session.query(Person.organisation_id, db.func.count(Person.id))
        .filter(Person.organisation_id.in_(ids))
        .group_by(Person.organisation_id)
        .all()
    ) if ids else {}
    session_counts = dict(
        active_sessions().join(Person, UserSession.person_id == Person.id)
        .filter(Person.organisation_id.in_(ids))
        .with_entities(Person.organisation_id, db.func.count(UserSession.id))
        .group_by(Person.organisation_id)
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

    if g.person and organisation.id == g.person.organisation_id:
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

    # a session works in one organisation, or none while its user chooses (or a platform admin)
    query = active_sessions().join(User, UserSession.user_id == User.id).outerjoin(Person, UserSession.person_id == Person.id)

    if search:
        query = query.filter(User.email.ilike(f'%{search}%'))

    if parse_uuid(organisation_id):
        query = query.filter(Person.organisation_id == parse_uuid(organisation_id))

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

    # users in the organisation, which they may share with others
    if parse_uuid(organisation_id):
        query = query.filter(User.people.any(Person.organisation_id == parse_uuid(organisation_id)))

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

### SUPPORT TICKETS ###
# raised from the Support page by people in any organisation. Notes are for platform admins only:
# the ticket's creator sees its status on the Support page, never the notes

@admin_bp.route('/support-tickets')
def support_tickets():
    search = request.args.get('search', '').strip()
    # unknown values show every ticket
    status = request.args.get('status', '').strip()
    status = status if status in SUPPORT_TICKET_STATUS_LABELS else ''
    organisation_id = request.args.get('organisation_id', '').strip()
    page = request.args.get('page', 1, type=int)

    query = SupportTicket.query.outerjoin(Person, SupportTicket.created_by_id == Person.id)

    if search:
        query = query.filter(db.or_(
            SupportTicket.description.ilike(f'%{search}%'),
            Person.email.ilike(f'%{search}%'),
            (Person.first_name + ' ' + Person.last_name).ilike(f'%{search}%'),
        ))

    if status:
        query = query.filter(SupportTicket.status == status)

    if parse_uuid(organisation_id):
        query = query.filter(SupportTicket.organisation_id == parse_uuid(organisation_id))

    # newest first
    pagination = query.order_by(SupportTicket.created_at.desc(), SupportTicket.id).paginate(
        page=page, per_page=25, error_out=False
    )

    return render_template(
        'admin/support_tickets/index.html',
        tickets=pagination.items,
        pagination=pagination,
        statuses=SUPPORT_TICKET_STATUSES,
        organisations=Organisation.query.order_by(Organisation.name, Organisation.id).all(),
        search=search,
        status=status,
        organisation_id=organisation_id,
    )

@admin_bp.route('/support-tickets/<uuid:id>')
def support_ticket(id):
    ticket = SupportTicket.query.get_or_404(id)
    return render_template('admin/support_tickets/view/overview.html', ticket=ticket, active_tab='overview')

@admin_bp.route('/support-tickets/<uuid:id>/close', methods=['POST'])
def close_support_ticket(id):
    ticket = SupportTicket.query.get_or_404(id)
    ticket.status = 'closed'
    db.session.commit()
    flash('Support ticket closed.', 'success')
    return back_to(url_for('admin.support_ticket', id=ticket.id))

@admin_bp.route('/support-tickets/<uuid:id>/reopen', methods=['POST'])
def reopen_support_ticket(id):
    ticket = SupportTicket.query.get_or_404(id)
    ticket.status = 'open'
    db.session.commit()
    flash('Support ticket reopened.', 'success')
    return back_to(url_for('admin.support_ticket', id=ticket.id))

# the Notes tab: the notes, oldest first, and a form to add one
@admin_bp.route('/support-tickets/<uuid:id>/notes', methods=['GET', 'POST'])
def support_ticket_notes(id):
    ticket = SupportTicket.query.get_or_404(id)
    form = SupportTicketNoteForm()

    if form.validate_on_submit():
        db.session.add(SupportTicketNote(description=form.description.data, support_ticket_id=ticket.id, created_by_id=g.user.id))
        db.session.commit()
        flash('Note added.', 'success')
        return redirect(url_for('admin.support_ticket_notes', id=ticket.id))

    return render_template('admin/support_tickets/view/notes.html', ticket=ticket, form=form, active_tab='notes')

# a note on another ticket is a 404, so ids can't be mixed
def ticket_note_or_404(id, note_id):
    ticket = SupportTicket.query.get_or_404(id)
    note = SupportTicketNote.query.filter_by(id=note_id, support_ticket_id=ticket.id).first_or_404()
    return ticket, note

@admin_bp.route('/support-tickets/<uuid:id>/notes/<uuid:note_id>/edit', methods=['GET', 'POST'])
def edit_support_ticket_note(id, note_id):
    ticket, note = ticket_note_or_404(id, note_id)
    form = SupportTicketNoteForm(obj=note)

    if form.validate_on_submit():
        note.description = form.description.data
        db.session.commit()
        flash('Note updated.', 'success')
        return redirect(url_for('admin.support_ticket_notes', id=ticket.id))

    return render_template('admin/support_tickets/note_edit.html', ticket=ticket, note=note, form=form)

@admin_bp.route('/support-tickets/<uuid:id>/notes/<uuid:note_id>/delete', methods=['GET', 'POST'])
def delete_support_ticket_note(id, note_id):
    ticket, note = ticket_note_or_404(id, note_id)

    if request.method == 'POST':
        db.session.delete(note)
        db.session.commit()
        flash('Note deleted.', 'success')
        return redirect(url_for('admin.support_ticket_notes', id=ticket.id))

    return render_template('admin/support_tickets/note_delete.html', ticket=ticket, note=note)
