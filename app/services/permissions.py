from app import db
from app.models import User, UserGroup, UserGroupPermission

# the resources and actions groups can be given. Keys are '<resource>.<action>', for example 'jobs.delete'
RESOURCES = [
    ('sites', 'Sites'),
    ('buildings', 'Buildings'),
    ('zones', 'Zones'),
    ('assets', 'Assets'),
    ('jobs', 'Jobs'),
    ('job_schedules', 'Job Schedules'),
    ('suppliers', 'Suppliers'),
    ('people', 'People'),
]
ACTIONS = [('view', 'View'), ('create', 'Create'), ('edit', 'Edit'), ('delete', 'Delete')]
# grantable permissions outside the resource × action grid, listed under it on the user group pages
EXTRA_PERMISSIONS = [
    ('jobs.close', 'Close jobs'),
    ('jobs.reopen', 'Reopen jobs'),
    # import any type of record, whatever else the group allows. Undoing an import also needs
    # delete on that type (checked in imports.undo)
    ('imports.manage', 'Import data'),
    # download every record of the types the group can view, as a zip of CSVs
    ('exports.manage', 'Export data'),
]
GRANTABLE = {f'{resource}.{action}' for resource, _ in RESOURCES for action, _ in ACTIONS} | {key for key, _ in EXTRA_PERMISSIONS}

# only the Admin group has these. They're never stored on a group or shown in the grid
ADMIN_ONLY = {
    'organisation.manage',  # update or delete the organisation (no routes yet)
    'sessions.manage',      # see and revoke everyone's sessions
    'user_groups.manage',   # create groups, change their permissions
}

TEAM_PERMISSIONS = GRANTABLE
NEW_GROUP_PERMISSIONS = {f'{resource}.view' for resource, _ in RESOURCES}


def _resource(blueprint, view=(), edit=()):
    endpoints = {
        f'{blueprint}.index': f'{blueprint}.view',
        f'{blueprint}.view': f'{blueprint}.view',
        f'{blueprint}.create': f'{blueprint}.create',
        f'{blueprint}.edit': f'{blueprint}.edit',
        f'{blueprint}.delete': f'{blueprint}.delete',
    }
    endpoints.update({f'{blueprint}.{name}': f'{blueprint}.view' for name in view})
    endpoints.update({f'{blueprint}.{name}': f'{blueprint}.edit' for name in edit})
    return endpoints

# the permission each endpoint needs, checked by require_permission. An endpoint in a protected
# blueprint that isn't listed here is refused, so a new route has to be added before it works.
# Related tabs and downloads need view, and linking or unlinking needs edit on the record you're on
ENDPOINT_PERMISSIONS = {
    **_resource('sites', view=['export', 'buildings', 'assets', 'jobs', 'job_schedules']),
    **_resource('buildings', view=['zones', 'assets', 'job_schedules']),
    **_resource('zones'),
    **_resource('assets', view=['export', 'jobs', 'job_schedules'], edit=['link_jobs', 'unlink_jobs', 'link_job_schedules', 'unlink_job_schedules']),
    **_resource('jobs', view=['export', 'assets'], edit=['link_assets', 'unlink_assets']),
    'jobs.close': 'jobs.close',
    'jobs.reopen': 'jobs.reopen',
    # part of closing a planned job, so it needs close rather than job_schedules.edit
    'jobs.update_schedule': 'jobs.close',
    **_resource('job_schedules', view=['export', 'jobs', 'assets'], edit=['link_assets', 'unlink_assets']),
    **_resource('suppliers', view=['export']),
    **_resource('people', view=['export'], edit=['resend_invite']),
    **{f'user_groups.{name}': 'user_groups.manage' for name in ['index', 'create', 'view', 'edit', 'delete']},
    **{f'imports.{name}': 'imports.manage' for name in ['index', 'upload', 'template', 'view', 'undo']},
    'exports.index': 'exports.manage',
}


def can(user, permission):
    if user is None or user.user_group is None:
        return False
    if user.user_group.is_admin:
        return True
    return permission in user.user_group.permission_keys


def clean_permissions(keys):
    """Keep only grantable keys, and add view wherever any other permission on that resource is given."""
    keys = set(keys) & GRANTABLE
    keys |= {f"{key.split('.')[0]}.view" for key in keys} & GRANTABLE
    return keys


def set_permissions(user_group, keys):
    keys = clean_permissions(keys)
    # remove and add the difference, rather than replacing the list, so an unchanged key isn't deleted and re-inserted
    for permission in [p for p in user_group.permissions if p.permission not in keys]:
        user_group.permissions.remove(permission)
    for key in sorted(keys - user_group.permission_keys):
        user_group.permissions.append(UserGroupPermission(permission=key))


def create_default_groups(organisation):
    """Add the Admin and Team groups for a new organisation. Doesn't commit."""
    admin = UserGroup(name='Admin', is_admin=True, organisation=organisation)
    team = UserGroup(name='Team', organisation=organisation)
    set_permissions(team, TEAM_PERMISSIONS)
    db.session.add_all([admin, team])
    return admin, team


def other_active_admins(user):
    """How many other users in the organisation are admins who can log in. Must stay above 0."""
    return (
        User.query.join(UserGroup, User.user_group_id == UserGroup.id)
        .filter(
            User.organisation_id == user.organisation_id,
            UserGroup.is_admin.is_(True),
            User.can_login.is_(True),
            User.id != user.id,
        )
        .count()
    )
