from app import db
from app.models import Person, UserGroup, UserGroupPermission

# the resources and actions groups can be given. Keys are '<resource>.<action>', for example 'jobs.delete'
RESOURCES = [
    ('locations', 'Locations'),
    ('assets', 'Assets'),
    ('jobs', 'Jobs'),
    ('planned_jobs', 'Planned Jobs'),
    ('suppliers', 'Suppliers'),
    ('people', 'People'),
    ('documents', 'Documents'),
]
ACTIONS = [('view', 'View'), ('create', 'Create'), ('edit', 'Edit'), ('delete', 'Delete')]
# grantable permissions outside the resource × action grid, listed under it on the user group pages
EXTRA_PERMISSIONS = [
    ('jobs.approve', 'Approve or reject new jobs'),
    # assigning, reassigning and adding helpers, and starting and completing a job for its assignee
    ('jobs.assign', 'Assign jobs, and start and complete them for the assignee'),
    ('jobs.close', 'Close jobs'),
    ('jobs.reopen', 'Reopen jobs'),
    # import any type of record, whatever else the group allows. Undoing an import also needs
    # delete on that type (checked in imports.undo)
    ('imports.manage', 'Import data'),
    # download every record of the types the group can view, as an Excel workbook
    ('exports.manage', 'Export data'),
    # the organisation's categories and conditions under /reference-data. Choosing one on a record
    # only needs edit on the record
    ('reference_data.manage', 'Manage reference data'),
]
# permissions in the grid that don't apply to their resource, shown as n/a. None at the moment: documents.edit
# covers a document's details (the stored file itself is never replaced)
NOT_GRANTABLE = set()
GRANTABLE = ({f'{resource}.{action}' for resource, _ in RESOURCES for action, _ in ACTIONS} - NOT_GRANTABLE) | {key for key, _ in EXTRA_PERMISSIONS}

# only the Admin group has these. They're never stored on a group or shown in the grid
ADMIN_ONLY = {
    'organisation.manage',  # My Organisation, including deleting it
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
    **_resource('locations', view=['export', 'assets', 'jobs', 'planned_jobs']),
    **_resource('assets', view=['export', 'sub_assets', 'jobs', 'planned_jobs'], edit=['assign', 'link_jobs', 'unlink_jobs', 'link_planned_jobs', 'unlink_planned_jobs']),
    **_resource('jobs', view=['export', 'assets', 'follow_up_jobs'], edit=['link_assets', 'unlink_assets']),
    'jobs.approve': 'jobs.approve',
    'jobs.approve_selected': 'jobs.approve',
    'jobs.reject': 'jobs.approve',
    # the job's assignee can open it, and start and complete it, without these (jobs.require_job_permission)
    **{f'jobs.{name}': 'jobs.assign' for name in ['assign', 'assign_selected', 'reassign', 'respond', 'schedule', 'add_helpers', 'remove_helper', 'start', 'complete']},
    'jobs.close': 'jobs.close',
    'jobs.reopen': 'jobs.reopen',
    # part of closing a planned job, so it needs close rather than planned_job_schedules.edit
    'jobs.update_schedule': 'jobs.close',
    **_resource('planned_jobs', view=['export', 'created', 'jobs', 'assets'], edit=['link_assets', 'unlink_assets']),
    **_resource('suppliers', view=['export']),
    **_resource('people', view=['export', 'assets'], edit=['resend_invite']),
    **{f'user_groups.{name}': 'user_groups.manage' for name in ['index', 'create', 'view', 'edit', 'delete']},
    **{f'imports.{name}': 'imports.manage' for name in ['index', 'upload', 'template', 'view', 'undo']},
    'exports.index': 'exports.manage',
    **{f'reference_data.{name}': 'reference_data.manage' for name in ['index', 'items', 'edit', 'delete', 'bulk']},
    # Download all documents: these also check documents.view
    'exports.request_documents': 'exports.manage',
    'exports.download_documents': 'exports.manage',
    **{f'my_organisation.{name}': 'organisation.manage' for name in ['view', 'edit', 'delete', 'delete_requested', 'confirm_delete']},
    # each record's Documents tab needs view on the record, and its route checks documents.view too
    **{f'{resource}.documents': f'{resource}.view' for resource in ['locations', 'assets', 'jobs', 'planned_jobs', 'people', 'suppliers']},
    # documents.create, link and unlink also check edit on the record the document is added to or removed from
    'documents.index': 'documents.view',
    'documents.view': 'documents.view',
    'documents.create': 'documents.create',
    'documents.edit': 'documents.edit',
    'documents.link': 'documents.view',
    'documents.unlink': 'documents.view',
    'documents.delete': 'documents.delete',
}


def can(person, permission):
    """person is the signed-in user's person in the organisation they're working in (g.person)."""
    if person is None or person.user_group is None:
        return False
    if person.user_group.is_admin:
        return True
    return permission in person.user_group.permission_keys


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


def other_active_admins(person):
    """How many other people in the person's organisation are admins who can log in. Must stay above 0."""
    return (
        Person.query.join(UserGroup, Person.user_group_id == UserGroup.id)
        .filter(
            Person.organisation_id == person.organisation_id,
            UserGroup.is_admin.is_(True),
            Person.can_login.is_(True),
            Person.id != person.id,
        )
        .count()
    )
