from datetime import datetime, timezone

from flask import url_for

from app.extensions import db
from app.models import Notification

# In-app notifications, under the bell in the navbar, for people who can sign in. Suppliers, and people who
# can't, are emailed instead (services/jobs.notify_assignee decides which)

# how many unread notifications the navbar's dropdown lists. The Notifications page has them all
NAVBAR_LIMIT = 5


def notify(person, message, job=None):
    """Add a notification for the person (doesn't commit). View opens the job."""
    notification = Notification(
        person=person,
        message=message,
        job=job,
        organisation_id=person.organisation_id,
    )
    db.session.add(notification)
    return notification


def person_notifications(person):
    """A query for the person's notifications, newest first."""
    return Notification.query.filter_by(person_id=person.id).order_by(Notification.created_at.desc(), Notification.id)


def unread_notifications(person):
    return person_notifications(person).filter(Notification.read_at.is_(None))


def awaiting_response(notification):
    """It's about a job issued to this person, who has yet to accept or decline it."""
    job = notification.job
    return job is not None and job.status == 'pending_acceptance' and job.is_assignee(notification.person)


def awaiting_date(notification):
    """It's about a job assigned to this person, who has yet to choose a date for it."""
    job = notification.job
    return job is not None and job.can_schedule and job.is_assignee(notification.person)


def notification_button(notification):
    if awaiting_response(notification):
        return 'Accept / Decline Job'
    return 'Schedule Job' if awaiting_date(notification) else 'View'


def notification_url(notification):
    """What its button opens."""
    if awaiting_response(notification):
        return url_for('jobs.respond', id=notification.job_id)
    if awaiting_date(notification):
        return url_for('jobs.schedule', id=notification.job_id)
    if notification.job_id:
        return url_for('jobs.view', id=notification.job_id)
    return url_for('notifications.index')


def mark_read(notifications):
    now = datetime.now(timezone.utc)
    for notification in notifications:
        if notification.read_at is None:
            notification.read_at = now
