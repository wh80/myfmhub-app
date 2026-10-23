import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from flask import current_app, render_template, url_for, request

from app import db
from app.models import OTP, User, UserSession
from app.services.email import send_email

OTP_EXPIRY_MINUTES = 15
OTP_MAX_ATTEMPTS = 5
OTP_RESEND_SECONDS = 60
OTP_MAX_PER_HOUR = 5
SESSION_EXPIRY_DAYS = 30
# last_seen_at is only written when it's older than this, so not every request writes
SESSION_TOUCH_MINUTES = 5
AUTH_COOKIE_NAME = 'auth_session'
NO_LOGIN_MESSAGE = "This account doesn't have sign-in access. Ask an admin in your organisation to turn it on."
ORGANISATION_SUSPENDED_MESSAGE = "Your organisation's account is suspended."


def hash_otp(code):
    pepper = current_app.config['OTP_PEPPER']
    if not pepper:
        raise RuntimeError('OTP_PEPPER is not set')
    return hmac.new(pepper.encode(), code.encode(), hashlib.sha256).hexdigest()


def as_utc(value):
    # Postgres returns timezone-aware values, SQLite returns naive ones in UTC
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def check_otp_rate_limit(user):
    """Returns an error message if the user has asked for too many codes recently, otherwise None."""
    now = datetime.now(timezone.utc)
    recent = OTP.query.filter(OTP.user_id == user.id)

    if recent.filter(OTP.created_at > now - timedelta(seconds=OTP_RESEND_SECONDS)).count():
        return 'We sent you a code less than a minute ago. Please wait a moment before asking for another.'
    if recent.filter(OTP.created_at > now - timedelta(hours=1)).count() >= OTP_MAX_PER_HOUR:
        return 'Too many codes have been requested for this account. Please try again in an hour.'
    return None


def create_and_send_otp(email, first_name, welcome=False, invited_by=None):
    user = User.query.filter_by(email=email.lower()).one()
    now = datetime.now(timezone.utc)

    # only the newest code is valid, so expire any unused ones. Old codes are kept for a day
    # because check_otp_rate_limit counts them
    OTP.query.filter(OTP.user_id == user.id, OTP.used_at.is_(None), OTP.expires_at > now).update({'expires_at': now}, synchronize_session=False)
    OTP.query.filter(OTP.user_id == user.id, OTP.created_at < now - timedelta(days=1)).delete(synchronize_session=False)

    code = f'{secrets.randbelow(1000000):06d}'
    otp = OTP(
        user=user,
        code_hash=hash_otp(code),
        expires_at=now + timedelta(minutes=OTP_EXPIRY_MINUTES),
    )
    db.session.add(otp)
    db.session.commit()

    html = render_template(
        'emails/auth/send_otp.html',
        first_name=first_name,
        code=code,
        validate_url=url_for('auth.validate', id=otp.id, _external=True),
        expiry_minutes=OTP_EXPIRY_MINUTES,
        welcome=welcome,
        invited_by=invited_by,
        organisation_name=user.organisation.name,
        login_url=url_for('auth.login', _external=True),
    )
    if invited_by:
        subject = f'{invited_by} added you to MyFmHub'
    elif welcome:
        subject = 'Welcome to MyFmHub'
    else:
        subject = 'Your MyFmHub sign-in code'
    send_email(user.email, subject, html)

    return otp


def verify_otp(otp, code):
    """Check a submitted code. Returns an error message, or None if the code is valid (the OTP is then used up)."""
    now = datetime.now(timezone.utc)

    if otp.user.organisation.suspended_at:
        return ORGANISATION_SUSPENDED_MESSAGE
    if not otp.user.can_login:
        return NO_LOGIN_MESSAGE
    if otp.used_at:
        return 'This code has already been used.'
    if as_utc(otp.expires_at) < now:
        return 'This code has expired.'
    if otp.attempts >= OTP_MAX_ATTEMPTS:
        return 'Too many incorrect attempts. This code can no longer be used.'

    if not hmac.compare_digest(otp.code_hash, hash_otp(code)):
        otp.attempts += 1
        db.session.commit()
        remaining = OTP_MAX_ATTEMPTS - otp.attempts
        if remaining == 0:
            return 'Too many incorrect attempts. This code can no longer be used.'
        return f"That code isn't right. You have {remaining} {'attempt' if remaining == 1 else 'attempts'} left."

    otp.used_at = now
    if not otp.user.email_verified_at:
        otp.user.email_verified_at = now
    db.session.commit()
    return None


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(user):
    """Save a new session for the user and return the raw token for the cookie."""
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    user_session = UserSession(
        user=user,
        token_hash=hash_token(token),
        expires_at=now + timedelta(days=SESSION_EXPIRY_DAYS),
        last_seen_at=now,
        user_agent=(request.headers.get('User-Agent') or '')[:255] or None,
        ip_address=request.remote_addr,
    )
    db.session.add(user_session)
    db.session.commit()
    return token


def set_session_cookie(response, token):
    response.set_cookie(
        AUTH_COOKIE_NAME,
        token,
        max_age=SESSION_EXPIRY_DAYS * 24 * 60 * 60,
        httponly=True,
        secure=current_app.config['AUTH_COOKIE_SECURE'],
        samesite='Lax',
    )
    return response


def get_active_session(token):
    """Return the UserSession for a token, or None if it's missing, expired or revoked, the user can't log in or their organisation is suspended."""
    if not token:
        return None

    now = datetime.now(timezone.utc)
    user_session = UserSession.query.filter_by(token_hash=hash_token(token), revoked_at=None).first()
    if (not user_session or as_utc(user_session.expires_at) < now
            or not user_session.user.can_login or user_session.user.organisation.suspended_at):
        return None

    if not user_session.last_seen_at or as_utc(user_session.last_seen_at) < now - timedelta(minutes=SESSION_TOUCH_MINUTES):
        user_session.last_seen_at = now
        db.session.commit()

    return user_session


def active_sessions():
    """Query for sessions that haven't been revoked or expired."""
    return UserSession.query.filter(UserSession.revoked_at.is_(None), UserSession.expires_at > datetime.now(timezone.utc))


def active_session_counts(users):
    """Return {user_id: number of active sessions} for the given users."""
    ids = [user.id for user in users]
    if not ids:
        return {}

    rows = (
        active_sessions()
        .filter(UserSession.user_id.in_(ids))
        .with_entities(UserSession.user_id, db.func.count(UserSession.id))
        .group_by(UserSession.user_id)
        .all()
    )
    return dict(rows)


def revoke_session(user_session):
    user_session.revoked_at = datetime.now(timezone.utc)
    db.session.commit()


def revoke_user_sessions(user):
    count = active_sessions().filter(UserSession.user_id == user.id).update(
        {'revoked_at': datetime.now(timezone.utc)}, synchronize_session=False
    )
    db.session.commit()
    return count


def revoke_organisation_sessions(organisation):
    user_ids = db.select(User.id).where(User.organisation_id == organisation.id)
    count = active_sessions().filter(UserSession.user_id.in_(user_ids)).update(
        {'revoked_at': datetime.now(timezone.utc)}, synchronize_session=False
    )
    db.session.commit()
    return count


def revoke_all_sessions():
    """Sign everyone out of every organisation, for example after a security incident."""
    count = active_sessions().update({'revoked_at': datetime.now(timezone.utc)}, synchronize_session=False)
    db.session.commit()
    return count


def suspend_organisation(organisation):
    """Stop everyone in the organisation signing in and end their sessions."""
    organisation.suspended_at = datetime.now(timezone.utc)
    db.session.commit()
    return revoke_organisation_sessions(organisation)


def reinstate_organisation(organisation):
    organisation.suspended_at = None
    db.session.commit()


def clear_session_cookie(response):
    response.delete_cookie(
        AUTH_COOKIE_NAME,
        httponly=True,
        secure=current_app.config['AUTH_COOKIE_SECURE'],
        samesite='Lax',
    )
    return response
