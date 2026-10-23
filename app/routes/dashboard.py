from datetime import timedelta
from flask import Blueprint, render_template, redirect, url_for, g
from app.models import Job
from app.routes.helpers import login_required, org_query
from app.services.jobs import overdue_conditions
from app.services.permissions import can
from app.services.scheduling import uk_today
dashboard_bp = Blueprint('dashboard', __name__, url_prefix="/dashboard")

@dashboard_bp.route('/')
@login_required
def index():
    # the job cards need jobs.view. Each count matches the filtered /jobs list its card links to
    if not can(g.user, 'jobs.view'):
        return render_template('dashboard/index.html', counts=None)

    today = uk_today()
    week_ahead = today + timedelta(days=7)
    jobs = org_query(Job)
    statutory = Job.statutory_compliance.is_(True)

    counts = {
        'overdue_compliance': jobs.filter(statutory, *overdue_conditions(today)).count(),
        'compliance_risks': jobs.filter(statutory, Job.due_by >= today, Job.due_by <= week_ahead, Job.status != 'closed').count(),
        'overdue': jobs.filter(*overdue_conditions(today)).count(),
        'new': jobs.filter(Job.status == 'new').count(),
    }

    return render_template('dashboard/index.html', counts=counts, today=today, week_ahead=week_ahead)

# served at the root / url, see create_app
def root():
    return redirect(url_for('dashboard.index'))
