from flask import Blueprint, render_template, redirect, url_for, request, flash, g, current_app
from app import db
from app.forms import OTPForm, LoginForm, SwitchOrganisationForm
from app.models import OTP, User, UserSession, Person
from app.routes.helpers import safe_next_url, login_required, user_required, revoke_and_redirect, parse_uuid
from app.services.permissions import can
from app.services.auth import (
    AUTH_COOKIE_NAME, ORGANISATION_SUSPENDED_MESSAGE, check_otp_rate_limit, create_and_send_otp, verify_otp, create_session,
    set_session_cookie, get_active_session, revoke_session, clear_session_cookie, active_sessions, signin_refusal,
    choose_person, switch_person,
)
auth_bp = Blueprint('auth', __name__, url_prefix="/auth")

def organisation_suspended():
    return render_template('auth/organisation_suspended.html', support_email=current_app.config['SUPPORT_EMAIL']), 403

def expired_link():
    # codes are deleted a day after they're sent, so old email links end up here
    flash('That sign-in link has expired. Enter your email to get a new code.', 'warning')
    return redirect(url_for('auth.login', next=safe_next_url(request.args.get('next'))))

@auth_bp.before_app_request
def load_user():
    # g.person is the signed-in user's person in the organisation this session is working in, and
    # everything organisation-scoped (org_query, can) goes through it. None until one is chosen
    g.user_session = get_active_session(request.cookies.get(AUTH_COOKIE_NAME))
    g.user = g.user_session.user if g.user_session else None
    g.person = g.user_session.person if g.user_session else None
    g.organisation = g.person.organisation if g.person else None

# served at the root /login url, see create_app
def login():
    next_url = safe_next_url(request.args.get('next'))

    if g.user:
        return redirect(next_url or url_for('dashboard.index'))

    form = LoginForm()

    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.lower()).first()

        if not user:
            form.email.errors.append("We couldn't find an account with this email.")
            return render_template('auth/login.html', form=form)

        refusal = signin_refusal(user)
        if refusal == ORGANISATION_SUSPENDED_MESSAGE:
            return organisation_suspended()
        if refusal:
            form.email.errors.append(refusal)
            return render_template('auth/login.html', form=form)

        error = check_otp_rate_limit(user)
        if error:
            form.email.errors.append(error)
            return render_template('auth/login.html', form=form)

        try:
            otp = create_and_send_otp(user)
        except Exception:
            current_app.logger.exception(f'Failed to send OTP to {user.email}')
            flash("We couldn't send your sign-in code. Please try again later.", 'warning')
            return render_template('auth/login.html', form=form)

        flash(f"We've sent a sign-in code to {user.email}.", 'success')
        return redirect(url_for('auth.validate', id=otp.id, next=next_url))

    return render_template('auth/login.html', form=form)

@auth_bp.route('/logout', methods=['POST'])
def logout():
    if g.user_session:
        revoke_session(g.user_session)
    flash("You've been signed out.", 'success')
    return clear_session_cookie(redirect(url_for('auth.login')))

@auth_bp.route('/validate/<uuid:id>', methods=['GET', 'POST'])
def validate(id):
    otp = db.session.get(OTP, id)

    if not otp:
        return expired_link()

    if signin_refusal(otp.user, otp) == ORGANISATION_SUSPENDED_MESSAGE:
        return organisation_suspended()

    form = OTPForm()

    if form.validate_on_submit():
        error = verify_otp(otp, form.code.data)

        if error:
            form.code.errors.append(error)
            return render_template('auth/validate.html', otp=otp, form=form)

        user = otp.user
        person = choose_person(user, otp)
        token = create_session(user, person)
        next_url = safe_next_url(request.args.get('next'))

        if person is not None:
            destination = next_url or url_for('dashboard.index')
        elif user.signin_people():
            destination = url_for('auth.switch_organisation', next=next_url)
        else:
            # a platform admin with no organisation
            destination = url_for('admin.index')
        return set_session_cookie(redirect(destination), token)

    return render_template('auth/validate.html', otp=otp, form=form)

@auth_bp.route('/validate/<uuid:id>/resend', methods=['POST'])
def resend(id):
    """Send a new code to the same user, for when the email didn't arrive or the code expired. It keeps the
    organisation the code was sent for, so a registration or invite code still goes there."""
    otp = db.session.get(OTP, id)

    if not otp:
        return expired_link()

    user = otp.user
    next_url = safe_next_url(request.args.get('next'))

    refusal = signin_refusal(user, otp)
    if refusal == ORGANISATION_SUSPENDED_MESSAGE:
        return organisation_suspended()
    if refusal:
        flash(refusal, 'warning')
        return redirect(url_for('auth.login'))

    error = check_otp_rate_limit(user)
    if error:
        flash(error, 'warning')
        return redirect(url_for('auth.validate', id=otp.id, next=next_url))

    try:
        new_otp = create_and_send_otp(user, otp.person)
    except Exception:
        current_app.logger.exception(f'Failed to send OTP to {user.email}')
        flash("We couldn't send a new code. Please try again in a few minutes.", 'warning')
        return redirect(url_for('auth.validate', id=otp.id, next=next_url))

    flash(f"We've sent a new code to {user.email}.", 'success')
    return redirect(url_for('auth.validate', id=new_otp.id, next=next_url))

### ORGANISATIONS ###
# a user can be a person in several organisations. The session works in one at a time

@auth_bp.route('/organisation', methods=['GET', 'POST'])
@user_required
def switch_organisation():
    people = g.user.signin_people()
    next_url = safe_next_url(request.args.get('next'))

    if not people:
        # only a platform admin gets here without an organisation
        return redirect(url_for('admin.index'))

    # the current organisation, or at sign-in the one they last worked in
    current = g.person or next((person for person in people if person.organisation_id == g.user.last_organisation_id), None)
    form = SwitchOrganisationForm(person_id=str(current.id) if current else '')
    form.person_id.choices = [(str(person.id), person.organisation.name) for person in people]

    if form.validate_on_submit():
        person = next(person for person in people if person.id == parse_uuid(form.person_id.data))
        switch_person(g.user_session, person)
        flash(f"You're now working in {person.organisation.name}.", 'success')
        # records belong to one organisation, so the page you were on may not exist in the new one
        return redirect(next_url if next_url and g.person is None else url_for('dashboard.index'))

    return render_template('auth/switch_organisation.html', form=form, people=people)

### SESSIONS ###
# admins (sessions.manage) see and revoke every session working in their organisation, plus their own.
# Everyone else sees their own sessions, in any organisation. Removing someone's access is done with
# can_login on their person record, not here

def org_sessions():
    query = active_sessions().outerjoin(Person, UserSession.person_id == Person.id)
    if can(g.person, 'sessions.manage'):
        return query.filter(db.or_(Person.organisation_id == g.person.organisation_id, UserSession.user_id == g.user.id))
    return query.filter(UserSession.user_id == g.user.id)

@auth_bp.route('/sessions')
@login_required
def sessions():
    user_sessions = org_sessions().order_by(UserSession.last_seen_at.desc(), UserSession.id).all()

    return render_template('auth/sessions.html', user_sessions=user_sessions)

@auth_bp.route('/sessions/<uuid:id>/revoke', methods=['POST'])
@login_required
def revoke(id):
    user_session = org_sessions().filter(UserSession.id == id).first_or_404()
    return revoke_and_redirect(user_session, url_for('auth.sessions'))
