from flask import Blueprint, render_template, redirect, url_for, request, flash, g, current_app, abort
from app import db
from app.forms import PersonForm
from app.models import Person, User, UserGroup
from app.routes.helpers import require_login, require_permission, org_query, org_get_or_404, parse_uuid
from app.services.exports import export_response
from app.services.auth import check_otp_rate_limit, create_and_send_otp, revoke_user_sessions
from app.services.permissions import can, other_active_admins
people_bp = Blueprint('people', __name__, url_prefix="/people")
people_bp.before_request(require_login)
people_bp.before_request(require_permission)

# every person has a user, which holds the login (email, can_login). Person.email and User.email
# must always match, so they're set together, and the person and user are created and deleted together

def email_taken(email, user=None):
    """True if another user anywhere already has this email (emails are unique across organisations)."""
    query = User.query.filter(User.email == email)
    if user:
        query = query.filter(User.id != user.id)
    return query.first() is not None

def group_choices(form):
    """Only admins can put someone in the Admin group."""
    query = org_query(UserGroup)
    if not g.user.is_org_admin:
        query = query.filter(UserGroup.is_admin.is_(False))
    form.user_group_id.choices = [('', '--- Select User Group ---')] + [
        (str(user_group.id), user_group.name)
        for user_group in query.order_by(UserGroup.is_admin.desc(), UserGroup.name).all()
    ]

def selected_group(form):
    return org_query(UserGroup).filter_by(id=parse_uuid(form.user_group_id.data)).first()

def require_can_manage(person):
    """Only admins can change or delete an admin."""
    if person.user.is_org_admin and not g.user.is_org_admin:
        abort(403)

def send_invite(person):
    """Email the person a sign-in code, telling them who added them. Flashes the outcome."""
    error = check_otp_rate_limit(person.user)
    if error:
        flash(f"Sign-in email not sent to {person.email}: {error}", 'warning')
        return

    inviter = g.user.person
    invited_by = f'{inviter.first_name} {inviter.last_name}' if inviter else g.user.email

    try:
        create_and_send_otp(person.email, person.first_name, invited_by=invited_by)
    except Exception:
        current_app.logger.exception(f'Failed to send invite to {person.email}')
        flash(f"We couldn't send the sign-in email to {person.email}. They can request a code from the sign-in page.", 'warning')
        return

    flash(f"Sign-in email sent to {person.email}.", 'success')

# the index table's columns, for downloads. Keys match data-col in people/index.html
PERSON_COLUMNS = [
    ('name', 'Name', lambda person: f'{person.first_name} {person.last_name}'),
    ('job_title', 'Job Title', lambda person: person.job_title),
    ('email', 'Email', lambda person: person.email),
    ('mobile', 'Mobile', lambda person: person.mobile),
    ('user_group', 'User Group', lambda person: person.user.user_group.name if person.user and person.user.user_group else None),
    ('can_log_in', 'Can log in', lambda person: bool(person.user and person.user.can_login)),
]

# the index filters, shared by the page and its downloads
def filtered_people():
    search = request.args.get('search', '').strip()

    query = org_query(Person)

    # set by "View imported items" on an import
    import_id = parse_uuid(request.args.get('import_id'))
    if import_id:
        query = query.filter(Person.import_event_id == import_id)

    if search:
        query = query.filter(
            db.or_(
                Person.first_name.ilike(f'%{search}%'),
                Person.last_name.ilike(f'%{search}%'),
                (Person.first_name + ' ' + Person.last_name).ilike(f'%{search}%'),
                Person.email.ilike(f'%{search}%'),
                Person.job_title.ilike(f'%{search}%')
            )
        )

    return query.order_by(Person.last_name, Person.first_name, Person.id), dict(search=search, import_id=import_id)

@people_bp.route('/')
def index():
    query, filters = filtered_people()
    page = request.args.get('page', 1, type=int)

    pagination = query.paginate(page=page, per_page=25, error_out=False)

    return render_template(
        'people/index.html',
        people=pagination.items,
        pagination=pagination,
        **filters
    )

@people_bp.route('/export/<any(csv, pdf):file_format>')
def export(file_format):
    query, _ = filtered_people()
    people = query.options(db.joinedload(Person.user).joinedload(User.user_group)).all()
    return export_response(file_format, 'people', 'People', PERSON_COLUMNS, people)

@people_bp.route('/create', methods=['GET', 'POST'])
def create():
    form = PersonForm()
    group_choices(form)

    if form.validate_on_submit():
        email = form.email.data.lower()

        if email_taken(email):
            form.email.errors.append('A user with this email already exists.')
            return render_template('people/create.html', form=form)

        user = User(
            email=email,
            can_login=form.can_login.data,
            user_group=selected_group(form),
            organisation_id=g.user.organisation_id,
        )
        person = Person(
            first_name=form.first_name.data,
            last_name=form.last_name.data,
            email=email,
            job_title=form.job_title.data,
            mobile=form.mobile.data,
            organisation_id=g.user.organisation_id,
            user=user,
        )
        db.session.add_all([user, person])
        db.session.commit()
        flash(f"Person '{person.first_name} {person.last_name}' created successfully!", 'success')

        if user.can_login:
            send_invite(person)

        return redirect(url_for('people.view', id=person.id))

    return render_template('people/create.html', form=form)

@people_bp.route('/<uuid:id>', methods=['GET'])
def view(id):
    person = org_get_or_404(Person, id)

    return render_template('people/view.html', person=person)

@people_bp.route('/<uuid:id>/edit', methods=['GET', 'POST'])
def edit(id):
    person = org_get_or_404(Person, id)
    user = person.user
    require_can_manage(person)

    form = PersonForm(obj=person, can_login=user.can_login, user_group_id=str(user.user_group_id) if user.user_group_id else '')
    group_choices(form)

    if form.validate_on_submit():
        email = form.email.data.lower()

        if email_taken(email, user):
            form.email.errors.append('A user with this email already exists.')
            return render_template('people/edit.html', person=person, form=form)

        if user.id == g.user.id and not form.can_login.data:
            form.can_login.errors.append("You can't remove your own sign-in access.")
            return render_template('people/edit.html', person=person, form=form)

        user_group = selected_group(form)

        if user.id == g.user.id and not g.user.is_org_admin and user_group != user.user_group:
            form.user_group_id.errors.append("You can't change your own user group.")
            return render_template('people/edit.html', person=person, form=form)

        stays_admin = user_group is not None and user_group.is_admin and form.can_login.data
        if user.is_org_admin and not stays_admin and not other_active_admins(user):
            form.user_group_id.errors.append('Your organisation needs at least one admin who can log in. Make someone else an admin first.')
            return render_template('people/edit.html', person=person, form=form)

        could_login = user.can_login

        person.first_name=form.first_name.data
        person.last_name=form.last_name.data
        person.job_title=form.job_title.data
        person.mobile=form.mobile.data
        person.email=email
        if user.email != email:
            user.email=email
            user.email_verified_at=None
        user.can_login=form.can_login.data
        user.user_group=user_group

        db.session.commit()
        flash(f"Person '{person.first_name} {person.last_name}' updated successfully!", 'success')

        if could_login and not user.can_login:
            revoke_user_sessions(user)
        elif user.can_login and not could_login:
            send_invite(person)

        # an admin may have just moved themselves into a group that can't see people
        if not can(g.user, 'people.view'):
            return redirect(url_for('dashboard.index'))
        return redirect(url_for('people.view', id=person.id))

    return render_template('people/edit.html', person=person, form=form)

@people_bp.route('/<uuid:id>/send-invite', methods=['POST'])
def resend_invite(id):
    person = org_get_or_404(Person, id)
    require_can_manage(person)

    if not person.user.can_login:
        flash(f"Turn on 'Can log in' for {person.first_name} before sending a sign-in email.", 'warning')
    else:
        send_invite(person)

    return redirect(url_for('people.view', id=person.id))

@people_bp.route('/<uuid:id>/delete', methods=['GET', 'POST'])
def delete(id):
    person = org_get_or_404(Person, id)
    require_can_manage(person)

    if person.user and person.user.id == g.user.id:
        flash("You can't delete yourself.", 'warning')
        return redirect(url_for('people.view', id=person.id))

    if person.user.is_org_admin and not other_active_admins(person.user):
        flash('Your organisation needs at least one admin who can log in. Make someone else an admin first.', 'warning')
        return redirect(url_for('people.view', id=person.id))

    if request.method =="POST":
        # the user goes too (and with it their sessions and codes), in the same commit
        if person.user:
            db.session.delete(person.user)
        db.session.delete(person)
        db.session.commit()
        flash(f"Person '{person.first_name} {person.last_name}' deleted successfully!", 'success')
        return redirect(url_for('people.index'))

    return render_template('people/delete.html', person=person)
