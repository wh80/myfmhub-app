from flask import Blueprint, render_template, redirect, url_for, request, flash, g, current_app
from app import db
from app.forms import OTPForm, LoginForm
from app.models import OTP, User, UserSession
from app.routes.helpers import safe_next_url, login_required, revoke_and_redirect
from app.services.permissions import can
from app.services.auth import (
    AUTH_COOKIE_NAME, NO_LOGIN_MESSAGE, check_otp_rate_limit, create_and_send_otp, verify_otp, create_session, set_session_cookie,
    get_active_session, revoke_session, clear_session_cookie, active_sessions,
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
    g.user_session = get_active_session(request.cookies.get(AUTH_COOKIE_NAME))
    g.user = g.user_session.user if g.user_session else None

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

        if user.organisation.suspended_at:
            return organisation_suspended()

        if not user.can_login:
            form.email.errors.append(NO_LOGIN_MESSAGE)
            return render_template('auth/login.html', form=form)

        error = check_otp_rate_limit(user)
        if error:
            form.email.errors.append(error)
            return render_template('auth/login.html', form=form)

        try:
            otp = create_and_send_otp(user.email, user.person.first_name if user.person else '')
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

    if otp.user.organisation.suspended_at:
        return organisation_suspended()

    form = OTPForm()

    if form.validate_on_submit():
        error = verify_otp(otp, form.code.data)

        if error:
            form.code.errors.append(error)
            return render_template('auth/validate.html', otp=otp, form=form)

        token = create_session(otp.user)
        next_url = safe_next_url(request.args.get('next'))
        return set_session_cookie(redirect(next_url or url_for('dashboard.index')), token)

    return render_template('auth/validate.html', otp=otp, form=form)

@auth_bp.route('/validate/<uuid:id>/resend', methods=['POST'])
def resend(id):
    """Send a new code to the same user, for when the email didn't arrive or the code expired."""
    otp = db.session.get(OTP, id)

    if not otp:
        return expired_link()

    user = otp.user
    next_url = safe_next_url(request.args.get('next'))

    if user.organisation.suspended_at:
        return organisation_suspended()

    if not user.can_login:
        flash(NO_LOGIN_MESSAGE, 'warning')
        return redirect(url_for('auth.login'))

    error = check_otp_rate_limit(user)
    if error:
        flash(error, 'warning')
        return redirect(url_for('auth.validate', id=otp.id, next=next_url))

    try:
        new_otp = create_and_send_otp(user.email, user.person.first_name if user.person else '')
    except Exception:
        current_app.logger.exception(f'Failed to send OTP to {user.email}')
        flash("We couldn't send a new code. Please try again in a few minutes.", 'warning')
        return redirect(url_for('auth.validate', id=otp.id, next=next_url))

    flash(f"We've sent a new code to {user.email}.", 'success')
    return redirect(url_for('auth.validate', id=new_otp.id, next=next_url))

### SESSIONS ###
# admins (sessions.manage) see and revoke every session in their organisation, everyone else only their own.
# Removing someone's access is done with can_login on their person record, not here

def org_sessions():
    query = active_sessions().join(User).filter(User.organisation_id == g.user.organisation_id)
    if not can(g.user, 'sessions.manage'):
        query = query.filter(UserSession.user_id == g.user.id)
    return query

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
