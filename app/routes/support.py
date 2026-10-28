from flask import Blueprint, render_template, redirect, url_for, flash, g, abort, current_app
from app import db
from app.forms import SupportTicketForm
from app.models import SupportTicket
from app.routes.helpers import require_user, login_required
from app.services.auth import OTP_EXPIRY_MINUTES, OTP_MAX_ATTEMPTS, OTP_MAX_PER_HOUR, SESSION_EXPIRY_DAYS
from app.services.scheduling import FREQUENCIES
from app.services.support import send_ticket_notification
support_bp = Blueprint('support', __name__, url_prefix="/support")
support_bp.before_request(require_user)

# help pages that explain how the system works. Every signed-in user can read them, whatever their user
# group, so there's no require_permission. They show no organisation data, so they don't need an
# organisation chosen either (require_user, not require_login): platform admins may have none

@support_bp.route('/')
def index():
    # your own tickets in the organisation you're working in, with their status but never their notes
    tickets = []
    if g.person:
        tickets = SupportTicket.query.filter(SupportTicket.created_by_id == g.person.id).order_by(SupportTicket.created_at.desc(), SupportTicket.id).all()
    return render_template('support/index.html', tickets=tickets)

# raising a ticket needs an organisation (the ticket belongs to it), so login_required, not just a user
@support_bp.route('/tickets/new', methods=['GET', 'POST'])
@login_required
def create_ticket():
    form = SupportTicketForm()

    if form.validate_on_submit():
        ticket = SupportTicket(
            description=form.description.data,
            created_by_id=g.person.id,
            organisation_id=g.person.organisation_id,
        )
        db.session.add(ticket)
        db.session.commit()
        # the ticket is saved either way: if the email fails it's still in /admin, so just log it
        try:
            send_ticket_notification(ticket)
        except Exception:
            current_app.logger.exception(f'Failed to send the notification for support ticket {ticket.id}')
        flash("Support ticket created. We'll be in touch by email.", 'success')
        return redirect(url_for('support.index'))

    return render_template('support/create_ticket.html', form=form)

@support_bp.route('/locations')
def locations():
    return render_template('support/locations.html')

@support_bp.route('/job-schedules')
def job_schedules():
    return render_template('support/job_schedules.html', frequencies=FREQUENCIES)

# for MyFmHub staff: how logins, organisations and sessions fit together. 404 for anyone else, like /admin
@support_bp.route('/authentication')
def authentication():
    if not g.user.is_platform_admin:
        abort(404)
    return render_template(
        'support/authentication.html',
        code_minutes=OTP_EXPIRY_MINUTES,
        code_attempts=OTP_MAX_ATTEMPTS,
        codes_per_hour=OTP_MAX_PER_HOUR,
        session_days=SESSION_EXPIRY_DAYS,
    )
