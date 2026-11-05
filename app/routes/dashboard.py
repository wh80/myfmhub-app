from datetime import timedelta
from flask import Blueprint, render_template, redirect, url_for, g
from app.models import Document, Job, JOB_FINISHED_STATUSES
from app.routes.helpers import login_required, org_query
from app.services.documents import EXPIRING_SOON_DAYS
from app.services.jobs import overdue_conditions
from app.services.permissions import can
from app.services.scheduling import uk_today
dashboard_bp = Blueprint('dashboard', __name__, url_prefix="/dashboard")

@dashboard_bp.route('/')
@login_required
def index():
    # the job cards need jobs.view and Expiring Documents needs documents.view. Each count matches the
    # filtered list its card links to
    today = uk_today()
    week_ahead = today + timedelta(days=7)
    expiring_to = today + timedelta(days=EXPIRING_SOON_DAYS)
    counts = {}

    if can(g.person, 'jobs.view'):
        jobs = org_query(Job)
        statutory = Job.statutory_compliance.is_(True)
        mine = jobs.filter(Job.assigned_person_id == g.person.id)
        counts.update(
            overdue_compliance=jobs.filter(statutory, *overdue_conditions(today)).count(),
            compliance_risks=jobs.filter(statutory, Job.due_by >= today, Job.due_by <= week_ahead, Job.status.notin_(JOB_FINISHED_STATUSES)).count(),
            overdue=jobs.filter(*overdue_conditions(today)).count(),
            new=jobs.filter(Job.status == 'new').count(),
            unassigned=jobs.filter(Job.status == 'unassigned').count(),
            completed=jobs.filter(Job.status == 'completed').count(),
            my_jobs=mine.filter(Job.status.notin_(JOB_FINISHED_STATUSES)).count(),
            my_overdue=mine.filter(*overdue_conditions(today)).count(),
        )

    if can(g.person, 'documents.view'):
        counts['expiring_documents'] = org_query(Document).filter(Document.expires >= today, Document.expires <= expiring_to).count()

    return render_template('dashboard/index.html', counts=counts, today=today, week_ahead=week_ahead,
                           expiring_to=expiring_to, expiring_days=EXPIRING_SOON_DAYS,
                           me=f'person:{g.person.id}')

# served at the root / url, see create_app
def root():
    return redirect(url_for('dashboard.index'))
