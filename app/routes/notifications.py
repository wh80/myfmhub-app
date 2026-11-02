from flask import Blueprint, render_template, redirect, url_for, request, flash, g, abort
from app import db
from app.models import Notification
from app.routes.helpers import require_login
from app.services.notifications import mark_read, notification_url, person_notifications, unread_notifications
notifications_bp = Blueprint('notifications', __name__, url_prefix="/notifications")
notifications_bp.before_request(require_login)

# The signed-in person's own notifications (the navbar's bell). Everyone has their own, whatever their user
# group, so there's no require_permission; each route only touches g.person's


def own_notification_or_404(id):
    notification = db.session.get(Notification, id)
    if notification is None or notification.person_id != g.person.id:
        abort(404)
    return notification


@notifications_bp.route('/')
def index():
    page = request.args.get('page', 1, type=int)
    pagination = person_notifications(g.person).paginate(page=page, per_page=25, error_out=False)
    return render_template('notifications/index.html', notifications=pagination.items, pagination=pagination)


# its View button: marks it read and opens what it's about
@notifications_bp.route('/<uuid:id>/open', methods=['POST'])
def open_notification(id):
    notification = own_notification_or_404(id)
    mark_read([notification])
    db.session.commit()
    return redirect(notification_url(notification))


@notifications_bp.route('/read-all', methods=['POST'])
def read_all():
    mark_read(unread_notifications(g.person))
    db.session.commit()
    flash('All notifications marked as read.', 'success')
    return redirect(url_for('notifications.index'))
