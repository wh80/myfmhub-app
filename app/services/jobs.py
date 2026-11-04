import logging

from flask import render_template

from app.extensions import db
from app.models import Job, PlannedJobSchedule, JOB_FINISHED_STATUSES
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
