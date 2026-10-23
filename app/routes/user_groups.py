from flask import Blueprint, render_template, redirect, url_for, request, flash, g
from app import db
from app.forms import UserGroupForm
from app.models import UserGroup, User
from app.routes.helpers import require_login, require_permission, org_query, org_get_or_404
from app.services.permissions import NEW_GROUP_PERMISSIONS, clean_permissions, set_permissions
user_groups_bp = Blueprint('user_groups', __name__, url_prefix="/user-groups")
user_groups_bp.before_request(require_login)
user_groups_bp.before_request(require_permission)

# the Admin group is created with the organisation and can't be renamed, changed or deleted.
# Other groups (Team and any the organisation adds) get their permissions from the grid

def name_taken(name, user_group=None):
    query = org_query(UserGroup).filter(db.func.lower(UserGroup.name) == name.lower())
    if user_group:
        query = query.filter(UserGroup.id != user_group.id)
    return query.first() is not None

@user_groups_bp.route('/')
def index():
    search = request.args.get('search', '').strip()
    page = request.args.get('page', 1, type=int)

    query = org_query(UserGroup)

    if search:
        query = query.filter(UserGroup.name.ilike(f'%{search}%'))

    pagination = query.order_by(UserGroup.is_admin.desc(), UserGroup.name, UserGroup.id).paginate(
        page=page, per_page=25, error_out=False
    )

    ids = [user_group.id for user_group in pagination.items]
    member_counts = dict(
        db.session.query(User.user_group_id, db.func.count(User.id))
        .filter(User.user_group_id.in_(ids))
        .group_by(User.user_group_id)
        .all()
    ) if ids else {}

    return render_template(
        'user_groups/index.html',
        user_groups=pagination.items,
        pagination=pagination,
        member_counts=member_counts,
        search=search
    )

@user_groups_bp.route('/create', methods=['GET', 'POST'])
def create():
    form = UserGroupForm()
    granted = clean_permissions(request.form.getlist('permissions')) if request.method == 'POST' else NEW_GROUP_PERMISSIONS

    if form.validate_on_submit():
        name = form.name.data.strip()

        if name_taken(name):
            form.name.errors.append('There is already a group with this name.')
            return render_template('user_groups/create.html', form=form, granted=granted)

        user_group = UserGroup(
            name=name,
            organisation_id=g.user.organisation_id,
        )
        set_permissions(user_group, granted)
        db.session.add(user_group)
        db.session.commit()
        flash(f"User group '{user_group.name}' created successfully!", 'success')
        return redirect(url_for('user_groups.view', id=user_group.id))

    return render_template('user_groups/create.html', form=form, granted=granted)

@user_groups_bp.route('/<uuid:id>', methods=['GET'])
def view(id):
    user_group = org_get_or_404(UserGroup, id)
    members = sorted(user_group.users, key=lambda user: (user.person.last_name, user.person.first_name) if user.person else ('', user.email))

    return render_template('user_groups/view.html', user_group=user_group, members=members)

@user_groups_bp.route('/<uuid:id>/edit', methods=['GET', 'POST'])
def edit(id):
    user_group = org_get_or_404(UserGroup, id)

    if user_group.is_admin:
        flash("The Admin group always has full access and can't be changed.", 'warning')
        return redirect(url_for('user_groups.view', id=user_group.id))

    form = UserGroupForm(obj=user_group)
    granted = clean_permissions(request.form.getlist('permissions')) if request.method == 'POST' else user_group.permission_keys

    if form.validate_on_submit():
        name = form.name.data.strip()

        if name_taken(name, user_group):
            form.name.errors.append('There is already a group with this name.')
            return render_template('user_groups/edit.html', user_group=user_group, form=form, granted=granted)

        user_group.name=name
        set_permissions(user_group, granted)

        db.session.commit()
        flash(f"User group '{user_group.name}' updated successfully!", 'success')
        return redirect(url_for('user_groups.view', id=user_group.id))

    return render_template('user_groups/edit.html', user_group=user_group, form=form, granted=granted)

@user_groups_bp.route('/<uuid:id>/delete', methods=['GET', 'POST'])
def delete(id):
    user_group = org_get_or_404(UserGroup, id)

    if user_group.is_admin:
        flash("The Admin group can't be deleted.", 'warning')
        return redirect(url_for('user_groups.view', id=user_group.id))

    if user_group.users:
        flash(f"Move the {len(user_group.users)} people in '{user_group.name}' to another group before deleting it.", 'warning')
        return redirect(url_for('user_groups.view', id=user_group.id))

    if request.method =="POST":
        db.session.delete(user_group)
        db.session.commit()
        flash(f"User group '{user_group.name}' deleted successfully!", 'success')
        return redirect(url_for('user_groups.index'))

    return render_template('user_groups/delete.html', user_group=user_group)
