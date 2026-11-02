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


def notification_url(notification):
    """What its View button opens."""
    if notification.job_id:
        return url_for('jobs.view', id=notification.job_id)
    return url_for('notifications.index')


def mark_read(notifications):
    now = datetime.now(timezone.utc)
    for notification in notifications:
        if notification.read_at is None:
            notification.read_at = now
