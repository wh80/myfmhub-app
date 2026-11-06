import logging
import uuid
from datetime import datetime, timezone

from flask import current_app, render_template, url_for
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.extensions import db
from app.models import Job, Person, PlannedJobSchedule, JOB_FINISHED_STATUSES
from app.services.auth import as_utc
from app.services.email import send_email
from app.services.notifications import notify
from app.services.scheduling import uk_today

logger = logging.getLogger(__name__)

# how long the links emailed to an assignee work. They stop sooner once the job moves on. Work can be
# booked in months ahead, so the Schedule Job, Start Job and Complete Job links last much longer
RESPONSE_LINK_DAYS = 30
WORK_LINK_DAYS = 365
LINK_TOKEN_SALT = 'job-link'


def link_serializer():
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'], salt=LINK_TOKEN_SALT)


def make_link_token(job, step):
    """For an emailed link, for step 'respond' (Accept / Reject Job), 'schedule' (Schedule Job, and the same link
    to reschedule), 'start' (Start Job) or 'complete' (Complete Job). It names this issue of the job, so reassigning or issuing the job again stops an earlier link working."""
    return link_serializer().dumps({'job': str(job.id), 'step': step, 'issued': as_utc(job.issued_at).timestamp()})


def job_for_link(token, step):
    """The job a link is for, or None when the link is tampered with, expired, for another step or out of date."""
    days = RESPONSE_LINK_DAYS if step == 'respond' else WORK_LINK_DAYS
    try:
        data = link_serializer().loads(token, max_age=days * 86400)
    except (BadSignature, SignatureExpired):
        return None
    job = db.session.get(Job, uuid.UUID(data['job']))
    if job is None or data.get('step') != step or job.issued_at is None or job.organisation.suspended_at:
        return None
    if as_utc(job.issued_at).timestamp() != data['issued']:
        return None
    at_step = {'respond': job.status == 'pending_acceptance', 'schedule': job.can_schedule or job.can_reschedule,
               'start': job.can_start, 'complete': job.can_complete}
    return job if at_step[step] else None


def tell_assigner(job, message, responder=None):
    """A notification for whoever assigned the job (the caller commits it), unless they're the one who did this."""
    assigner = job.assigned_by
    if assigner is not None and assigner != responder and assigner.can_sign_in():
        notify(assigner, message, job)


def set_work_status(job):
    """Where a job with its assignee goes next: called when it's assigned, accepted or given a date."""
    if job.require_scheduling and job.scheduled_for is None:
        job.status = 'pending_scheduling'
    elif job.require_start_confirmation or (job.scheduled_for and job.scheduled_for > uk_today()):
        job.status = 'pending_start'
    else:
        job.status = 'in_progress'


def start_jobs_scheduled_for_today():
    """The daily run: jobs that don't need start confirmation go to in_progress when their date arrives, across
    every organisation. <= so a missed run is caught. Returns how many."""
    count = Job.query.filter(
        Job.status == 'pending_start',
        Job.require_start_confirmation.is_(False),
        Job.scheduled_for <= uk_today(),
    ).update({'status': 'in_progress'}, synchronize_session=False)
    db.session.commit()
    return count


def schedule_job(job, scheduled_for, responder=None):
    """Save the assignee's date. Returns True when it's a change to a date they'd already chosen, which whoever
    assigned the job is told about. responder is the person doing it, or None for the emailed link."""
    rescheduling = job.can_reschedule
    changed = rescheduling and scheduled_for != job.scheduled_for
    job.scheduled_for = scheduled_for
    set_work_status(job)
    if changed:
        tell_assigner(job, f"{job.assignee_name} rescheduled a job to {scheduled_for:%d-%m-%Y}: {job.description}", responder)
    return rescheduling


def start_job(job):
    job.status = 'in_progress'
    job.started_at = datetime.now(timezone.utc)


def complete_job(job, completed_at, comments):
    job.status = 'completed'
    job.completed_at = completed_at
    job.completion_comments = comments


def accept_job(job):
    set_work_status(job)
    job.assigned_at = datetime.now(timezone.utc)


def tell_next_step(job, after, responder=None):
    """After a job is accepted, scheduled or started (after), tell the assignee what's next (the caller
    commits any notification). Not when they did it in the app themselves: they're on the job page, with its
    next button. Returns False when they couldn't be told. The step still stands."""
    if job.is_assignee(responder):
        return True
    try:
        return notify_assignee(job, after=after)
    except Exception:
        logger.exception('Sending job next step email failed')
        return False


def reject_assignment(job, responder=None):
    """The assignee turned the job down: it goes back to unassigned, to be assigned afresh, and whoever
    assigned it gets a notification (the caller commits it), unless they're the one rejecting it.
    responder is the person doing it, or None for the emailed link."""
    tell_assigner(job, f"{job.assignee_name} rejected a job you issued: {job.description}", responder)

    job.status = 'unassigned'
    job.assigned_supplier = None
    job.assigned_person = None
    job.assigned_at = None
    job.assigned_by = None
    job.issued_at = None
    job.require_start_confirmation = False
    job.require_scheduling = False
    job.scheduled_for = None
    job.helpers = []


def notify_assignee(job, assigned_by=None, after=None):
    """Tell the job's new assignee it's been assigned to them (Assign and Reassign): an in-app notification
    for a person who can sign in (the caller commits it), otherwise an email with the job's details, for a
    supplier or a person who can't sign in. assigned_by is the assigner's name. Returns False when there's
    no email address to send to. Raises if the email fails. An issued job asks them to accept or reject it:
    the notification opens jobs.respond, and the email has a link that needs no sign-in. Otherwise the email
    has a link for the job's next step, Schedule Job, Start Job or Complete Job, and a Reschedule link once
    there's a date. after ('accepted', 'scheduled' or 'started') means this is the follow-up that carries
    those links, rather than the news of the job. People who can sign in use the buttons on the job's page."""
    person = job.assigned_person
    issued = job.status == 'pending_acceptance'
    if person is not None and person.can_sign_in():
        if after:
            if job.can_schedule:
                next_step = "Choose the date you'll do the work"
            else:
                next_step = 'Start it when the work begins' if job.can_start else "Complete it when the work's done"
            message = f"Your job has been {after} for you. {next_step}: {job.description}"
        elif issued:
            message = f"{assigned_by} issued you a job to accept or reject: {job.description}"
        elif job.can_schedule:
            message = f"{assigned_by} assigned you a job to schedule: {job.description}"
        else:
            message = f"{assigned_by} assigned you a job: {job.description}"
        notify(person, message, job)
        return True
    email = job.assignee.email
    if not email:
        return False
    organisation_name = job.organisation.name
    respond_url = url_for('job_responses.respond', token=make_link_token(job, 'respond'), _external=True) if issued else None
    schedule_url = url_for('job_responses.schedule', token=make_link_token(job, 'schedule'), _external=True) if job.can_schedule or job.can_reschedule else None
    start_url = url_for('job_responses.start', token=make_link_token(job, 'start'), _external=True) if job.can_start else None
    complete_url = url_for('job_responses.complete', token=make_link_token(job, 'complete'), _external=True) if job.can_complete else None
    html = render_template('emails/jobs/assigned.html', job=job, assignee_name=job.assignee_name, organisation_name=organisation_name,
                           respond_url=respond_url, schedule_url=schedule_url, start_url=start_url, complete_url=complete_url, after=after,
                           link_days=RESPONSE_LINK_DAYS if respond_url else WORK_LINK_DAYS)
    link = 'Schedule' if job.can_schedule else 'Start' if start_url else 'Complete'
    subject = f'{link} link for your job from {organisation_name}' if after else f'New job from {organisation_name}'
    send_email(email, f'{subject}: {job.description}', html)
    return True


def notify_previous_assignee(job, assignee, assignee_name, reassigned_by):
    """Tell whoever a job was reassigned from that they're no longer required, so a supplier doesn't turn up for
    nothing: a notification for a person who can sign in (the caller commits it), otherwise an email. The notification
    has no job link, because they may not be able to open the job any more. reassigned_by is the name of the person
    who did it. Returns False when there's no email address to send to. Raises if the email fails."""
    if isinstance(assignee, Person) and assignee.can_sign_in():
        notify(assignee, f"{reassigned_by} reassigned a job, so you're no longer required on it: {job.description}")
        return True
    if not assignee.email:
        return False
    organisation_name = job.organisation.name
    html = render_template('emails/jobs/reassigned.html', job=job, assignee_name=assignee_name, organisation_name=organisation_name)
    send_email(assignee.email, f'Job no longer required by {organisation_name}: {job.description}', html)
    return True


# past its due date and not closed or rejected. A job due today isn't overdue yet. Pass uk_today()
def overdue_conditions(today):
    return Job.due_by < today, Job.status.notin_(JOB_FINISHED_STATUSES)


# <= rather than ==, so a missed cron run, or a schedule saved with a creation date already
# in the past, is still picked up on the next run. A NULL creation date (a job raised and not
# yet closed) never matches, and inactive schedules are skipped
def due_planned_job_schedules(today):
    return PlannedJobSchedule.query.filter(PlannedJobSchedule.next_job_creation_date <= today, PlannedJobSchedule.is_active.is_(True))


# raises one job for the schedule's current due date. next_due_by is left as it is and the creation
# date is cleared, so the schedule raises nothing more until the job is closed
def raise_job(planned_job_schedule):
    job = Job(
        description=planned_job_schedule.description,
        work_required=planned_job_schedule.work_required,
        due_by=planned_job_schedule.next_due_by,
        statutory_compliance=planned_job_schedule.statutory_compliance,
        status='new',
        source='planned',
        location_id=planned_job_schedule.location_id,
        job_category_id=planned_job_schedule.job_category_id,
        estimated_cost=planned_job_schedule.estimated_cost,
        organisation_id=planned_job_schedule.organisation_id,
        planned_job_schedule_id=planned_job_schedule.id,
    )
    job.assets = list(planned_job_schedule.assets)
    db.session.add(job)

    planned_job_schedule.next_job_creation_date = None
    return job


# returns (created, failed): descriptions of the jobs raised, and of the schedules that errored.
# Each schedule commits on its own, so one bad schedule doesn't stop the rest
def create_scheduled_jobs(today=None):
    today = today or uk_today()
    planned_job_schedule_ids = [
        planned_job_schedule_id for (planned_job_schedule_id,) in due_planned_job_schedules(today)
        .with_entities(PlannedJobSchedule.id)
        .order_by(PlannedJobSchedule.next_job_creation_date, PlannedJobSchedule.id)
    ]

    created = []
    failed = []
    for planned_job_schedule_id in planned_job_schedule_ids:
        # lock the row and check it's still due, so two runs at once can't both raise it.
        # SQLite ignores the lock
        planned_job_schedule = (
            due_planned_job_schedules(today)
            .filter(PlannedJobSchedule.id == planned_job_schedule_id)
            .with_for_update(skip_locked=True)
            .first()
        )
        if planned_job_schedule is None:
            continue

        # one job at a time: raising clears the creation date, so even a schedule that's behind
        # raises nothing more until its job is closed
        try:
            description = f"'{planned_job_schedule.description}' ({planned_job_schedule.id}) due {planned_job_schedule.next_due_by:%d-%m-%Y}"
            raise_job(planned_job_schedule)
            db.session.commit()
            created.append(description)
        except Exception:
            db.session.rollback()
            logger.exception('Failed to raise a job for planned job schedule %s', planned_job_schedule_id)
            failed.append(f'{planned_job_schedule_id}')

    return created, failed
