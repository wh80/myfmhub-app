import logging

from app.extensions import db
from app.models import Job, JobSchedule
from app.services.scheduling import job_creation_date, next_due_date, uk_today

logger = logging.getLogger(__name__)

# a year of daily jobs
MAX_JOBS_PER_SCHEDULE_RUN = 366


# past its due date and not closed. A job due today isn't overdue yet. Pass uk_today()
def overdue_conditions(today):
    return Job.due_by < today, Job.status != 'closed'


# <= rather than ==, so a missed cron run, or a schedule saved with a creation date already
# in the past, is still picked up on the next run
def due_job_schedules(today):
    return JobSchedule.query.filter(JobSchedule.next_job_creation_date <= today)


# raises one job for the schedule's current due date, then moves the schedule on by its frequency
def raise_job(job_schedule):
    job = Job(
        summary=job_schedule.summary,
        work_required=job_schedule.work_required,
        due_by=job_schedule.next_due_by,
        statutory_compliance=job_schedule.statutory_compliance,
        status='new',
        source='planned',
        site_id=job_schedule.site_id,
        organisation_id=job_schedule.organisation_id,
        job_schedule_id=job_schedule.id,
    )
    job.assets = list(job_schedule.assets)
    db.session.add(job)

    job_schedule.next_due_by = next_due_date(job_schedule.next_due_by, job_schedule.frequency)
    job_schedule.next_job_creation_date = job_creation_date(job_schedule.next_due_by, job_schedule.notice_days)
    return job


# returns (created, failed): descriptions of the jobs raised, and of the schedules that errored.
# Each schedule commits on its own, all of its jobs or none, so one bad schedule doesn't stop the rest
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

        # if runs were missed, raise every occurrence that's now due, not just the first.
        # The cap stops a schedule set far in the past flooding the jobs list in one run;
        # anything left over is raised on the next run
        descriptions = []
        try:
            while job_schedule.next_job_creation_date <= today and len(descriptions) < MAX_JOBS_PER_SCHEDULE_RUN:
                descriptions.append(f"'{job_schedule.summary}' ({job_schedule.id}) due {job_schedule.next_due_by:%d-%m-%Y}")
                raise_job(job_schedule)
            db.session.commit()
            created.extend(descriptions)
            if job_schedule.next_job_creation_date <= today:
                logger.warning('Job schedule %s hit the limit of %s jobs in one run and is still behind', job_schedule_id, MAX_JOBS_PER_SCHEDULE_RUN)
        except Exception:
            db.session.rollback()
            logger.exception('Failed to raise a job for job schedule %s', job_schedule_id)
            failed.append(f'{job_schedule_id}')

    return created, failed
