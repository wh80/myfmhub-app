from flask import Blueprint, render_template, g, abort
from app.routes.helpers import require_user
from app.services.auth import OTP_EXPIRY_MINUTES, OTP_MAX_ATTEMPTS, OTP_MAX_PER_HOUR, SESSION_EXPIRY_DAYS
from app.services.scheduling import FREQUENCIES
support_bp = Blueprint('support', __name__, url_prefix="/support")
support_bp.before_request(require_user)

# help pages that explain how the system works. Every signed-in user can read them, whatever their user
# group, so there's no require_permission. They show no organisation data, so they don't need an
# organisation chosen either (require_user, not require_login): platform admins may have none

@support_bp.route('/')
def index():
    return render_template('support/index.html')

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
