from flask import Blueprint, render_template, request
from app import db
from app.forms import JobCompleteForm, JobScheduleForm
from app.services.auth import as_utc
from app.services.jobs import accept_job, complete_job, job_for_link, reject_assignment, schedule_job, start_job, tell_next_step
job_responses_bp = Blueprint('job_responses', __name__, url_prefix="/job-response")

# The Accept / Reject Job, Schedule Job, Start Job and Complete Job links emailed to suppliers and people who can't sign in, so they
# need no login: the signed token is the permission. Opening one changes nothing, so a mail scanner following
# links can't respond, and it stops working once the job moves on or goes to someone else


@job_responses_bp.route('/<token>', methods=['GET', 'POST'])
def respond(token):
    job = job_for_link(token, 'respond')

    if job is None:
        return render_template('jobs/respond.html', state='invalid')

    state = None
    link_emailed = False
    if request.method == 'POST':
        response = request.form.get('response')
        if response == 'accept':
            accept_job(job)
            state = 'accepted'
        elif response == 'reject':
            reject_assignment(job)
            state = 'rejected'
        db.session.commit()
        if state == 'accepted':
            link_emailed = tell_next_step(job, 'accepted')
            db.session.commit()

    return render_template('jobs/respond.html', job=job, state=state, link_emailed=link_emailed)


# the same link chooses the date and changes it later, until the work starts
@job_responses_bp.route('/<token>/schedule', methods=['GET', 'POST'])
def schedule(token):
    job = job_for_link(token, 'schedule')

    if job is None:
        return render_template('jobs/respond.html', state='invalid')

    form = JobScheduleForm(due_by=job.due_by, scheduled_for=job.scheduled_for)

    if form.validate_on_submit():
        rescheduling = schedule_job(job, form.scheduled_for.data)
        db.session.commit()
        link_emailed = False
        if not rescheduling:
            link_emailed = tell_next_step(job, 'scheduled')
            db.session.commit()
        return render_template('jobs/respond.html', job=job, state='scheduled', step='schedule', link_emailed=link_emailed)

    return render_template('jobs/schedule.html', job=job, form=form, public=True)


@job_responses_bp.route('/<token>/start', methods=['GET', 'POST'])
def start(token):
    job = job_for_link(token, 'start')

    if job is None:
        return render_template('jobs/respond.html', state='invalid')

    state = None
    link_emailed = False
    if request.method == 'POST':
        start_job(job)
        db.session.commit()
        state = 'started'
        link_emailed = tell_next_step(job, 'started')
        db.session.commit()

    return render_template('jobs/respond.html', job=job, state=state, step='start', link_emailed=link_emailed)


@job_responses_bp.route('/<token>/complete', methods=['GET', 'POST'])
def complete(token):
    job = job_for_link(token, 'complete')

    if job is None:
        return render_template('jobs/respond.html', state='invalid')

    form = JobCompleteForm(started_at=as_utc(job.started_at) if job.started_at else None)

    if form.validate_on_submit():
        complete_job(job, form.completed_at_uk(), form.completion_comments.data)
        db.session.commit()
        return render_template('jobs/respond.html', job=job, state='completed', step='complete')

    return render_template('jobs/complete.html', job=job, form=form, public=True)
