from flask import Blueprint, render_template, redirect, url_for, request, flash, g, current_app
from app.models import Site, Building, Zone, Asset, Job, JobSchedule, Supplier, Person, UserGroup, ImportEvent, File
from app.services.files import storage_text, storage_used_mb
from app.routes.helpers import require_login, require_permission, org_query
from app.services.auth import clear_session_cookie
from app.services.organisations import DELETE_LINK_EXPIRY_HOURS, delete_organisation, read_delete_token, send_delete_email
my_organisation_bp = Blueprint('my_organisation', __name__, url_prefix="/my-organisation")
my_organisation_bp.before_request(require_login)
my_organisation_bp.before_request(require_permission)

# the organisation the session is working in. Every route needs organisation.manage, so admins only.
# Deleting takes three steps: tick every box on the warning page, which emails the admin a link, then
# confirm on the page that link opens (see services/organisations.py)

# what deleting removes, each with a box to tick on the warning page: (key, label, model)
DELETE_ITEMS = [
    ('sites', 'Sites', Site),
    ('buildings', 'Buildings', Building),
    ('zones', 'Zones', Zone),
    ('assets', 'Assets', Asset),
    ('jobs', 'Jobs', Job),
    ('job_schedules', 'Job Schedules', JobSchedule),
    ('suppliers', 'Suppliers', Supplier),
    ('people', 'People (logins used only in this organisation are deleted too)', Person),
    ('user_groups', 'User Groups', UserGroup),
    ('imports', 'Import history', ImportEvent),
    ('files', 'Files (removed from storage too)', File),
]
# and one more box, with no count
IRREVERSIBLE = ('irreversible', 'I understand that deleting this data is permanent and cannot be undone')


def delete_items():
    return [(key, label, org_query(model).count()) for key, label, model in DELETE_ITEMS]


@my_organisation_bp.route('/')
def view():
    return render_template('my_organisation/view.html', organisation=g.organisation, items=delete_items(),
                           storage_used=storage_text(storage_used_mb(g.organisation.id)))

@my_organisation_bp.route('/delete', methods=['GET', 'POST'])
def delete():
    items = delete_items()
    required = {key for key, _, _ in items} | {IRREVERSIBLE[0]}

    if request.method == 'POST':
        ticked = set(request.form.getlist('confirm'))
        if not required <= ticked:
            flash('Tick every box to confirm you agree to deleting this data.', 'warning')
            return render_template('my_organisation/delete.html', items=items, irreversible=IRREVERSIBLE, ticked=ticked)

        try:
            send_delete_email(g.person)
        except Exception:
            current_app.logger.exception(f'Failed to send delete confirmation to {g.person.email}')
            flash("We couldn't send the confirmation email. Please try again in a few minutes.", 'warning')
            return render_template('my_organisation/delete.html', items=items, irreversible=IRREVERSIBLE, ticked=ticked)
        return redirect(url_for('my_organisation.delete_requested'))

    return render_template('my_organisation/delete.html', items=items, irreversible=IRREVERSIBLE, ticked=set())

@my_organisation_bp.route('/delete/requested')
def delete_requested():
    return render_template('my_organisation/delete_requested.html', expiry_hours=DELETE_LINK_EXPIRY_HOURS)

# the link in the email. It only works for the admin who asked, signed in to this organisation
@my_organisation_bp.route('/delete/confirm/<token>', methods=['GET', 'POST'])
def confirm_delete(token):
    data = read_delete_token(token)
    if data is None:
        flash('That delete link has expired or is invalid. Start again from My Organisation if you still want to delete it.', 'warning')
        return redirect(url_for('my_organisation.view'))
    if data['organisation'] != str(g.person.organisation_id) or data['person'] != str(g.person.id):
        flash('That delete link is for a different organisation or person. Sign in as the admin who asked for it, in that organisation, and open the link again.', 'warning')
        return redirect(url_for('my_organisation.view'))

    if request.method == 'POST':
        name = g.organisation.name
        delete_organisation(g.organisation)
        # the session's person has gone (and its user, if this was their only organisation), so sign out
        flash(f"{name} and all its data have been deleted.", 'success')
        response = redirect(url_for('auth.login'))
        clear_session_cookie(response)
        return response

    return render_template('my_organisation/confirm_delete.html', organisation=g.organisation, items=delete_items(), token=token)
