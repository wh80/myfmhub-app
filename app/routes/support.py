from flask import Blueprint, render_template
from app.routes.helpers import require_login
from app.services.scheduling import FREQUENCIES
support_bp = Blueprint('support', __name__, url_prefix="/support")
support_bp.before_request(require_login)

# help pages that explain how the system works. Every signed-in user can read them, whatever
# their user group, so there's no require_permission

@support_bp.route('/')
def index():
    return render_template('support/index.html')

@support_bp.route('/locations')
def locations():
    return render_template('support/locations.html')

@support_bp.route('/job-schedules')
def job_schedules():
    return render_template('support/job_schedules.html', frequencies=FREQUENCIES)
