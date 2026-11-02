import logging

from flask import render_template

from app.extensions import db
from app.models import Job, JobSchedule, JOB_FINISHED_STATUSES
from app.services.email import send_email
from app.services.notifications import notify
from app.services.scheduling import uk_today

logger = logging.getLogger(__name__)


def notify_assignee(job, assigned_by):
    """Tell the job's new assignee it's been assigned to them (Assign and Reassign): an in-app notification
    for a person who can sign in (the caller commits it), otherwise an email with the job's details, for a
    supplier or a person who can't sign in. assigned_by is the assigner's name. Returns False when there's
    no email address to send to. Raises if the email fails."""
    person = job.assigned_person
    if person is not None and person.can_sign_in():
        notify(person, f"{assigned_by} assigned you a job: {job.description}", job)
        return True
    email = job.assignee.email
    if not email:
        return False
    organisation_name = job.organisation.name
    html = render_template('emails/jobs/assigned.html', job=job, assignee_name=job.assignee_name, organisation_name=organisation_name)
    send_email(email, f'New job from {organisation_name}: {job.description}', html)
    return True


# past its due date and not closed or rejected. A job due today isn't overdue yet. Pass uk_today()
def overdue_conditions(today):
    return Job.due_by < today, Job.status.notin_(JOB_FINISHED_STATUSES)


# <= rather than ==, so a missed cron run, or a schedule saved with a creation date already
# in the past, is still picked up on the next run. A NULL creation date (a job raised and not
# yet closed) never matches, and inactive schedules are skipped
def due_job_schedules(today):
    return JobSchedule.query.filter(JobSchedule.next_job_creation_date <= today, JobSchedule.is_active.is_(True))


# raises one job for the schedule's current due date. next_due_by is left as it is and the creation
# date is cleared, so the schedule raises nothing more until the job is closed
def raise_job(job_schedule):
    job = Job(
        description=job_schedule.description,
        work_required=job_schedule.work_required,
        due_by=job_schedule.next_due_by,
        statutory_compliance=job_schedule.statutory_compliance,
        status='new',
        source='planned',
        site_id=job_schedule.site_id,
        building_id=job_schedule.building_id,
        zone_id=job_schedule.zone_id,
        job_category_id=job_schedule.job_category_id,
        estimated_cost=job_schedule.estimated_cost,
        organisation_id=job_schedule.organisation_id,
        job_schedule_id=job_schedule.id,
    )
    job.assets = list(job_schedule.assets)
    db.session.add(job)

    job_schedule.next_job_creation_date = None
    return job


# returns (created, failed): descriptions of the jobs raised, and of the schedules that errored.
# Each schedule commits on its own, so one bad schedule doesn't stop the rest
def create_scheduled_jobs(today=None):
    today = today or uk_today()
    job_schedule_ids = [
        job_schedule_id for (job_schedule_id,) in due_job_schedules(today)
        .with_entities(JobSchedule.id)
        .order_by(JobSchedule.next_job_creation_date, JobSchedule.id)
    ]

    created = []
    failed = []
    for job_schedule_id in job_schedule_ids:
        # lock the row and check it's still due, so two runs at once can't both raise it.
        # SQLite ignores the lock
        job_schedule = (
            due_job_schedules(today)
            .filter(JobSchedule.id == job_schedule_id)
            .with_for_update(skip_locked=True)
            .first()
        )
        if job_schedule is None:
            continue

        # one job at a time: raising clears the creation date, so even a schedule that's behind
        # raises nothing more until its job is closed
        try:
            description = f"'{job_schedule.description}' ({job_schedule.id}) due {job_schedule.next_due_by:%d-%m-%Y}"
            raise_job(job_schedule)
            db.session.commit()
            created.append(description)
        except Exception:
            db.session.rollback()
            logger.exception('Failed to raise a job for job schedule %s', job_schedule_id)
            failed.append(f'{job_schedule_id}')

    return created, failed
