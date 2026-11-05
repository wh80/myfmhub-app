from datetime import datetime, timezone
from flask import Blueprint, render_template, redirect, url_for, request, flash, g, current_app, abort
from app import db
from app.forms import JobForm, JobRejectForm, JobAssignForm, JobReassignForm, JobHelpersForm, JobCompleteForm, JobScheduleForm, PlannedJobScheduleNextDueForm
from app.models import Job, Asset, Notification, Person, Supplier, JobCategory, ASSIGNED_JOB_STATUSES, JOB_STATUSES, JOB_STATUS_LABELS, JOB_FINISHED_STATUSES, JOB_SOURCES, JOB_SOURCE_LABELS
from app.services.auth import as_utc
from app.services.jobs import accept_job, complete_job, overdue_conditions, notify_assignee, reject_assignment, schedule_job, set_work_status, start_job, tell_next_step
from app.services.notifications import mark_read
from app.services.permissions import can
from app.services.scheduling import job_creation_date, next_due_date, uk_today
from app.routes.helpers import back_to, safe_next_url, selected_ids, parse_date, require_login, require_permission, org_query, org_get_or_404, parse_uuid, set_location_choices, create_from, location_options, filter_location, same_top_location, documents_tab, related, related_page, set_reference_choices, reference_items, filter_reference
from app.services.exports import export_response
jobs_bp = Blueprint('jobs', __name__, url_prefix="/jobs")
jobs_bp.before_request(require_login)

# the job's assignee (a person) can open it, and start and complete it, whatever their user group allows,
# so someone with no jobs permissions can still do a job assigned to them
ASSIGNEE_ENDPOINTS = {'jobs.view', 'jobs.respond', 'jobs.schedule', 'jobs.start', 'jobs.complete'}

def require_job_permission():
    """require_permission, letting the job's assignee through to ASSIGNEE_ENDPOINTS."""
    if request.endpoint in ASSIGNEE_ENDPOINTS:
        job = org_query(Job).filter_by(id=request.view_args.get('id')).first()
        if job is not None and job.is_assignee(g.person):
            return None
    return require_permission()

jobs_bp.before_request(require_job_permission)

# the index table's columns, for downloads. Keys match data-col in jobs/job_list.html
JOB_COLUMNS = [
    ('description', 'Description', lambda job: job.description),
    ('location', 'Location', lambda job: job.location.materialised_path),
    ('category', 'Job Category', lambda job: job.job_category.description if job.job_category else None),
    ('due_by', 'Due By', lambda job: job.due_by),
    ('source', 'Source', lambda job: job.source_label),
    ('status', 'Status', lambda job: job.status_label),
    ('assigned_to', 'Assigned To', lambda job: job.assignee_name),
    ('closed_on', 'Closed On', lambda job: job.closed_at),
    ('statutory', 'Statutory', lambda job: job.statutory_compliance),
]

# the index filters, shared by the page and its downloads
def filtered_jobs():
    search = request.args.get('search', '').strip()
    statutory = request.args.get('statutory') == '1'
    overdue = request.args.get('overdue') == '1'
    # unknown values are ignored rather than matching nothing.
    # 'not_closed' is a filter-only option for every status except closed and rejected
    status = request.args.get('status', '').strip()
    status = status if status in JOB_STATUS_LABELS or status == 'not_closed' else ''
    source = request.args.get('source', '').strip()
    source = source if source in JOB_SOURCE_LABELS else ''
    due_from = parse_date(request.args.get('due_from'))
    due_to = parse_date(request.args.get('due_to'))
    closed_from = parse_date(request.args.get('closed_from'))
    closed_to = parse_date(request.args.get('closed_to'))
    # supplier:<id> or person:<id>, as in the assign page's select
    assigned_to = request.args.get('assigned_to', '').strip()
    assignee_kind, _, assignee_id = assigned_to.partition(':')
    assignee_id = parse_uuid(assignee_id)
    assigned_to = assigned_to if assignee_kind in ('supplier', 'person') and assignee_id else ''

    # the Assigned To column
    query = org_query(Job).options(
        db.selectinload(Job.assigned_supplier),
        db.selectinload(Job.assigned_person),
    )

    # set by "View imported items" on an import
    import_id = parse_uuid(request.args.get('import_id'))
    if import_id:
        query = query.filter(Job.import_event_id == import_id)

    if search:
        query = query.filter(
            db.or_(
                Job.description.ilike(f'%{search}%'),
                Job.work_required.ilike(f'%{search}%')
            )
        )

    if statutory:
        query = query.filter(Job.statutory_compliance.is_(True))

    if overdue:
        query = query.filter(*overdue_conditions(uk_today()))

    if status == 'not_closed':
        query = query.filter(Job.status.notin_(JOB_FINISHED_STATUSES))
    elif status:
        query = query.filter(Job.status == status)

    if source:
        query = query.filter(Job.source == source)

    if assigned_to:
        column = Job.assigned_supplier_id if assignee_kind == 'supplier' else Job.assigned_person_id
        query = query.filter(column == assignee_id)

    # date ranges include both ends. Jobs with no date drop out once either end is set
    if due_from:
        query = query.filter(Job.due_by >= due_from)

    if due_to:
        query = query.filter(Job.due_by <= due_to)

    if closed_from:
        query = query.filter(Job.closed_at >= closed_from)

    if closed_to:
        query = query.filter(Job.closed_at <= closed_to)

    query, location = filter_location(query, Job)
    query, job_category_id = filter_reference(query, Job, 'job_category_id')

    filters = dict(
        job_category_id=job_category_id,
        search=search,
        import_id=import_id,
        statutory=statutory,
        overdue=overdue,
        status=status,
        source=source,
        assigned_to=assigned_to,
        due_from=due_from,
        due_to=due_to,
        closed_from=closed_from,
        closed_to=closed_to,
        **location,
    )
    return query.order_by(Job.description, Job.id), filters

@jobs_bp.route('/')
def index():
    query, filters = filtered_jobs()
    page = request.args.get('page', 1, type=int)

    pagination = query.paginate(page=page, per_page=25, error_out=False)

    # approving every job on the last page of a filtered list leaves that page empty
    if pagination.pages and page > pagination.pages:
        return redirect(url_for('jobs.index', **dict(request.args.to_dict(), page=pagination.pages)))

    # the Assigned to filter only lists what the group can already see, plus you: the dashboard's My Jobs cards use it
    suppliers, people = assignee_options()
    if not can(g.person, 'people.view'):
        people = [option for option in people if option[0] == f'person:{g.person.id}']

    return render_template(
        'jobs/index.html',
        jobs=pagination.items,
        pagination=pagination,
        **location_options(),
        job_categories=reference_items(JobCategory),
        statuses=JOB_STATUSES,
        sources=JOB_SOURCES,
        suppliers=suppliers if can(g.person, 'suppliers.view') else [],
        people=people,
        **filters
    )

@jobs_bp.route('/export/<any(xlsx, pdf):file_format>')
def export(file_format):
    query, _ = filtered_jobs()
    jobs = query.options(db.joinedload(Job.location)).all()
    return export_response(file_format, 'jobs', 'Jobs', JOB_COLUMNS, jobs)

@jobs_bp.route('/create', methods=['GET', 'POST'])
def create():
    # ?parent_id= creates a follow-up job for that job (the Create button on its Follow Up Jobs tab). It
    # starts at the parent's location
    parent = None
    if request.args.get('parent_id'):
        parent = org_get_or_404(Job, parse_uuid(request.args['parent_id']))

    # ?asset_id= (an asset's Jobs tab) links the new job to the asset, and ?location_id= (a location's Jobs
    # tab) puts it there. Either fixes the location: the select is hidden and what's posted is ignored
    asset, fixed_location = create_from()

    form = JobForm()
    set_location_choices(form)
    set_reference_choices(form)
    if fixed_location:
        form.location_id.data = str(fixed_location.id)
    elif parent and request.method == 'GET':
        form.location_id.data = str(parent.location_id)

    # where saving (and the Back button) goes: the tab or page the create was started from
    if asset:
        back_url = url_for('assets.jobs', id=asset.id)
    elif fixed_location:
        back_url = url_for('locations.jobs', id=fixed_location.id)
    elif parent:
        back_url = url_for('jobs.follow_up_jobs', id=parent.id)
    else:
        back_url = url_for('jobs.index')

    if form.validate_on_submit():
        job = Job(
            description=form.description.data,
            work_required=form.work_required.data,
            due_by=form.due_by.data,
            source='follow_up' if parent else 'reactive',
            parent_job=parent,
            location_id=parse_uuid(form.location_id.data),
            job_category_id=parse_uuid(form.job_category_id.data),
            # costs aren't on the create page: they're added on edit
            organisation_id=g.person.organisation_id,
        )
        if asset:
            job.assets.append(asset)
        db.session.add(job)
        db.session.commit()
        flash(f"Job '{job.description}' created successfully!", 'success')
        return redirect(back_url)

    return render_template('jobs/create.html', form=form, parent=parent, from_asset=asset, fixed_location=fixed_location, back_url=back_url)

@jobs_bp.route('/<uuid:id>', methods=['GET'])
def view(id):
    job = org_get_or_404(Job, id)

    # opening a job assigned to you reads its notifications
    mark_read(Notification.query.filter_by(person_id=g.person.id, job_id=job.id, read_at=None))
    db.session.commit()

    return render_template('jobs/view/overview.html', job=job, active_tab="overview")

@jobs_bp.route('/<uuid:id>/edit', methods=['GET', 'POST'])
def edit(id):
    job = org_get_or_404(Job, id)

    form=JobForm(obj=job)
    set_location_choices(form)
    set_reference_choices(form)

    if form.validate_on_submit():
        
        job.description=form.description.data
        job.work_required=form.work_required.data
        # a planned job is due when its schedule said, so its due date can't be changed
        if job.source != 'planned':
            job.due_by=form.due_by.data
        job.location_id=parse_uuid(form.location_id.data)
        job.job_category_id=parse_uuid(form.job_category_id.data)
        job.estimated_cost=form.estimated_cost.data
        job.actual_cost=form.actual_cost.data
                
        db.session.commit()
        flash(f"Job '{job.work_required}' updated successfully!", 'success')
        return redirect(url_for('jobs.view', id=job.id))

    return render_template('jobs/edit.html', job=job, form=form)

@jobs_bp.route('/<uuid:id>/delete', methods=['GET', 'POST'])
def delete(id):
    job = org_get_or_404(Job, id)

    # its schedule is waiting for it to close, so deleting it would stop the schedule raising jobs
    if job.source == 'planned' and job.status not in JOB_FINISHED_STATUSES:
        flash(f"Job '{job.description}' can't be deleted because it's an open planned job. Close it instead.", 'warning')
        return redirect(url_for('jobs.view', id=job.id))

    if request.method =="POST":
        db.session.delete(job)
        db.session.commit()
        flash(f"Job '{job.description}' deleted successfully!", 'success')
        return redirect(url_for('jobs.index'))

    return render_template('jobs/delete.html', job=job)

# approving a new job makes it unassigned, ready to be assigned
def approve_job(job):
    job.status = 'unassigned'
    job.approved_at = datetime.now(timezone.utc)

@jobs_bp.route('/<uuid:id>/approve', methods=['POST'])
def approve(id):
    job = org_get_or_404(Job, id)

    if job.status != 'new':
        flash(f"Job '{job.description}' can't be approved because it isn't new.", 'warning')
        return redirect(url_for('jobs.view', id=job.id))

    approve_job(job)
    db.session.commit()
    flash(f"Job '{job.description}' approved. Use Assign to give it to a supplier or a person.", 'success')
    return redirect(url_for('jobs.view', id=job.id))

def job_count(count):
    return '1 job' if count == 1 else f'{count} jobs'

def wrong_status(count, action, status):
    return f"{job_count(count)} couldn't be {action} because {'it is' if count == 1 else 'they are'} not {status}."

def selected_jobs():
    return org_query(Job).filter(Job.id.in_(selected_ids('job_ids'))).order_by(Job.description, Job.id).all()

# the Actions button on the index. Selected jobs may have moved on already, so those are counted, not refused
@jobs_bp.route('/approve-selected', methods=['POST'])
def approve_selected():
    jobs = selected_jobs()

    if not jobs:
        flash('Select at least one job.', 'warning')
        return back_to(url_for('jobs.index'))

    new_jobs = [job for job in jobs if job.status == 'new']
    for job in new_jobs:
        approve_job(job)
    db.session.commit()

    messages = []
    if new_jobs:
        messages.append(f'{job_count(len(new_jobs))} approved.')
    refused = len(jobs) - len(new_jobs)
    if refused:
        messages.append(wrong_status(refused, 'approved', 'new'))
    flash(' '.join(messages), 'warning' if refused else 'success')
    return back_to(url_for('jobs.index'))

# rejecting a new job. A planned job's schedule is waiting on it, so the schedule moves on from the date it's
# rejected, as it does from the completed date when a job is closed, and both are saved together
@jobs_bp.route('/<uuid:id>/reject', methods=['GET', 'POST'])
def reject(id):
    job = org_get_or_404(Job, id)

    if job.status != 'new':
        flash(f"Job '{job.description}' can't be rejected because it isn't new.", 'warning')
        return redirect(url_for('jobs.view', id=job.id))

    planned_job_schedule = job.planned_job_schedule if job.closes_with_schedule_update else None
    calculated = next_due_date(uk_today(), planned_job_schedule.frequency) if planned_job_schedule else None
    form = JobRejectForm(latest=calculated, data={'next_due_by': calculated} if request.method == 'GET' else None)

    if form.validate_on_submit():
        job.status = 'rejected'
        job.rejection_reason = form.rejection_reason.data
        job.rejected_at = datetime.now(timezone.utc)
        message = f"Job '{job.description}' rejected."
        if planned_job_schedule:
            planned_job_schedule.next_due_by = form.next_due_by.data
            planned_job_schedule.next_job_creation_date = job_creation_date(planned_job_schedule.next_due_by, planned_job_schedule.notice_days)
            message += f" Planned Job Schedule '{planned_job_schedule.description}' next due {planned_job_schedule.next_due_by:%d-%m-%Y}."
        db.session.commit()
        flash(message, 'success')
        return redirect(url_for('jobs.index'))

    return render_template('jobs/reject.html', job=job, form=form, planned_job_schedule=planned_job_schedule, calculated=calculated)

def person_name(person):
    return f'{person.first_name} {person.last_name}'

def assignee_options(exclude=None):
    """The suppliers and people a job can be assigned to, as (value, label) lists for the grouped select in
    jobs/_assignee_select.html, leaving out exclude (the current assignee). Values are 'supplier:<id>' or
    'person:<id>'."""
    suppliers = [(f'supplier:{supplier.id}', supplier.name)
                 for supplier in org_query(Supplier).order_by(Supplier.name, Supplier.id) if supplier is not exclude]
    people = [(f'person:{person.id}', f'{person_name(person)} ({person.email})')
              for person in org_query(Person).order_by(Person.first_name, Person.last_name, Person.id) if person is not exclude]
    return suppliers, people

def set_assignee_choices(form, exclude=None):
    suppliers, people = assignee_options(exclude)
    form.assignee.choices = [('', '-')] + suppliers + people
    return suppliers, people

def set_helper_choices(field, job=None):
    """Everyone in the organisation who isn't helping on the job already."""
    helping = {person.id for person in job.helpers} if job else set()
    field.choices = [(str(person.id), f'{person_name(person)} ({person.email})')
                     for person in org_query(Person).order_by(Person.first_name, Person.last_name, Person.id)
                     if person.id not in helping]

def chosen_assignee(value):
    """The supplier or person an assignee select value names. WTForms has already checked it's one of the
    choices, so it's this organisation's."""
    kind, _, id = value.partition(':')
    return org_get_or_404(Supplier if kind == 'supplier' else Person, parse_uuid(id))

def chosen_people(ids):
    ids = [parse_uuid(id) for id in ids]
    return org_query(Person).filter(Person.id.in_(ids)).all() if ids else []

def give_job_to(job, assignee):
    """Assign (or reassign) the job to a supplier or a person, recording who did it and when (issued_at). The
    caller sets assigned_at, unless it's waiting on acceptance. A person who was helping stops being a helper."""
    job.assigned_supplier = assignee if isinstance(assignee, Supplier) else None
    job.assigned_person = assignee if isinstance(assignee, Person) else None
    job.issued_at = datetime.now(timezone.utc)
    job.assigned_by = g.person
    if assignee in job.helpers:
        job.helpers.remove(assignee)

def assign_job(job, assignee, helpers, form):
    """Require Acceptance waits for the assignee to accept it, and assigned_at is set then. Otherwise it
    goes straight to its next step."""
    give_job_to(job, assignee)
    job.require_start_confirmation = form.require_start_confirmation.data
    job.require_scheduling = form.require_scheduling.data
    job.helpers.extend(person for person in helpers if person not in job.helpers)
    if form.require_acceptance.data:
        job.status = 'pending_acceptance'
    else:
        set_work_status(job)
        job.assigned_at = job.issued_at

def tell_assignee(jobs):
    """Notify the new assignee of each job, one notification or email per job (after the jobs are committed),
    and flash how it went. All the jobs went to the same assignee. They stay assigned if an email fails or
    there's no address to send to. Nobody is told when you assign a job to yourself: the job page you're
    sent to would mark the notification read straight away."""
    if jobs[0].assigned_person == g.person:
        return
    assignee_name = jobs[0].assignee_name
    failed = 0
    no_address = False
    for job in jobs:
        try:
            if not notify_assignee(job, person_name(g.person)):
                no_address = True
        except Exception:
            current_app.logger.exception('Sending job assigned email failed')
            failed += 1
    # the in-app notifications
    db.session.commit()
    if failed:
        emails = 'The email' if failed == 1 else f'{failed} emails'
        flash(f"{emails} to {assignee_name} couldn't be sent, so let them know yourself.", 'warning')
    if no_address:
        flash(f"{assignee_name} has no email address, so they haven't been told. Let them know yourself.", 'warning')

# Assign: gives an unassigned job to one supplier or one person, plus any helpers (people). Only the assignee
# is told: a notification for a person who can sign in, otherwise an email with the job's details
@jobs_bp.route('/<uuid:id>/assign', methods=['GET', 'POST'])
def assign(id):
    job = org_get_or_404(Job, id)

    if job.status != 'unassigned':
        flash(f"Job '{job.description}' can't be assigned because it's {job.status_label.lower()}.", 'warning')
        return redirect(url_for('jobs.view', id=job.id))

    form = JobAssignForm()
    suppliers, people = set_assignee_choices(form)
    set_helper_choices(form.helper_ids, job)

    if form.validate_on_submit():
        assign_job(job, chosen_assignee(form.assignee.data), chosen_people(form.helper_ids.data), form)
        db.session.commit()
        flash(f"Job '{job.description}' {'issued' if form.require_acceptance.data else 'assigned'} to {job.assignee_name}.", 'success')
        tell_assignee([job])
        return redirect(url_for('jobs.view', id=job.id))

    return render_template('jobs/assign.html', job=job, form=form, suppliers=suppliers, people=people, reassign=False)

# the Actions button on the index posts the ticked jobs here, which shows the assign page. That page posts them
# back with confirmed=1, so the statuses are checked again in case someone else has assigned them meanwhile
@jobs_bp.route('/assign-selected', methods=['POST'])
def assign_selected():
    back_url = safe_next_url(request.form.get('next')) or url_for('jobs.index')
    selected = selected_jobs()

    if not selected:
        flash('Select at least one job.', 'warning')
        return redirect(back_url)

    jobs = [job for job in selected if job.status == 'unassigned']
    refused = wrong_status(len(selected) - len(jobs), 'assigned', 'Unassigned') if len(jobs) < len(selected) else None

    if not jobs:
        flash(refused, 'warning')
        return redirect(back_url)

    form = JobAssignForm()
    suppliers, people = set_assignee_choices(form)
    set_helper_choices(form.helper_ids)

    if 'confirmed' in request.form and form.validate_on_submit():
        assignee = chosen_assignee(form.assignee.data)
        helpers = chosen_people(form.helper_ids.data)
        for job in jobs:
            assign_job(job, assignee, helpers, form)
        db.session.commit()
        assigned = f"{job_count(len(jobs))} {'issued' if form.require_acceptance.data else 'assigned'} to {jobs[0].assignee_name}."
        flash(f'{assigned} {refused}' if refused else assigned, 'warning' if refused else 'success')
        tell_assignee(jobs)
        return redirect(back_url)

    return render_template('jobs/assign.html', jobs=jobs, selected=selected, refused=refused, back_url=back_url,
                           form=form, suppliers=suppliers, people=people, reassign=False)

# Reassign: gives the job to someone else, at any stage after it's assigned and before it's closed. The
# status, dates and helpers stay as they are. The new assignee is told; the old one isn't
@jobs_bp.route('/<uuid:id>/reassign', methods=['GET', 'POST'])
def reassign(id):
    job = org_get_or_404(Job, id)

    if job.status not in ASSIGNED_JOB_STATUSES:
        flash(f"Job '{job.description}' can't be reassigned because it's {job.status_label.lower()}.", 'warning')
        return redirect(url_for('jobs.view', id=job.id))

    form = JobReassignForm()
    suppliers, people = set_assignee_choices(form, exclude=job.assignee)

    if form.validate_on_submit():
        previous = job.assignee_name
        give_job_to(job, chosen_assignee(form.assignee.data))
        # a job pending acceptance is still waiting on an answer: now from the new assignee, and the old one's link stops working
        if job.status != 'pending_acceptance':
            job.assigned_at = job.issued_at
        db.session.commit()
        flash(f"Job '{job.description}' reassigned from {previous} to {job.assignee_name}.", 'success')
        tell_assignee([job])
        return redirect(url_for('jobs.view', id=job.id))

    return render_template('jobs/assign.html', job=job, form=form, suppliers=suppliers, people=people, reassign=True)

# Add Helpers: more people helping on an assigned job. Helpers aren't told
@jobs_bp.route('/<uuid:id>/helpers/add', methods=['GET', 'POST'])
def add_helpers(id):
    job = org_get_or_404(Job, id)

    if job.status not in ASSIGNED_JOB_STATUSES:
        flash(f"Helpers can't be added to job '{job.description}' because it's {job.status_label.lower()}.", 'warning')
        return redirect(url_for('jobs.view', id=job.id))

    form = JobHelpersForm()
    # the assignee can't help on their own job
    set_helper_choices(form.helper_ids, job)
    form.helper_ids.choices = [choice for choice in form.helper_ids.choices if choice[0] != str(job.assigned_person_id)]

    if form.validate_on_submit():
        helpers = chosen_people(form.helper_ids.data)
        job.helpers.extend(helpers)
        db.session.commit()
        flash(f"Added {', '.join(person_name(person) for person in helpers)} to job '{job.description}' as helpers.", 'success')
        return redirect(url_for('jobs.view', id=job.id))

    return render_template('jobs/add_helpers.html', job=job, form=form)

# the remove button next to each helper on the overview. They aren't told
@jobs_bp.route('/<uuid:id>/helpers/<uuid:person_id>/remove', methods=['POST'])
def remove_helper(id, person_id):
    job = org_get_or_404(Job, id)

    if job.status not in ASSIGNED_JOB_STATUSES:
        flash(f"Helpers can't be removed from job '{job.description}' because it's {job.status_label.lower()}.", 'warning')
        return redirect(url_for('jobs.view', id=job.id))

    helper = next((person for person in job.helpers if person.id == person_id), None)
    if helper is None:
        abort(404)

    job.helpers.remove(helper)
    db.session.commit()
    flash(f"Removed {person_name(helper)} from job '{job.description}' as a helper.", 'success')
    return redirect(url_for('jobs.view', id=job.id))

def tell_about_next_step(job, after):
    told = tell_next_step(job, after, g.person)
    db.session.commit()
    if not told:
        flash(f"{job.assignee_name} couldn't be sent the link for the next step, so let them know yourself.", 'warning')

def not_at_step(job, action):
    flash(f"Job '{job.description}' can't be {action} because it's {job.status_label.lower()}.", 'warning')
    return redirect(url_for('jobs.view', id=job.id))

# Accept / Reject Job on an issued job, for the assignee (require_job_permission), or someone with jobs.assign
# responding for them, for example a supplier who rang up. Suppliers use job_responses.respond from their email
@jobs_bp.route('/<uuid:id>/respond', methods=['GET', 'POST'])
def respond(id):
    job = org_get_or_404(Job, id)

    if job.status != 'pending_acceptance':
        return not_at_step(job, 'accepted or rejected')

    mark_read(Notification.query.filter_by(person_id=g.person.id, job_id=job.id, read_at=None))
    db.session.commit()

    response = request.form.get('response')
    if request.method == 'POST' and response == 'accept':
        accept_job(job)
        db.session.commit()
        flash(f"Job '{job.description}' accepted.", 'success')
        tell_about_next_step(job, 'accepted')
        return redirect(url_for('jobs.view', id=job.id))

    if request.method == 'POST' and response == 'reject':
        reject_assignment(job, g.person)
        db.session.commit()
        flash(f"Job '{job.description}' rejected. It's back to Unassigned.", 'success')
        # the assignee may only have been able to see it because it was theirs
        return redirect(url_for('jobs.view', id=job.id) if can(g.person, 'jobs.view') else url_for('dashboard.index'))

    return render_template('jobs/respond.html', job=job, on_behalf=not job.is_assignee(g.person))

# Schedule Job and Reschedule, for the assignee (require_job_permission), or someone with jobs.assign doing it for
# them. Suppliers use job_responses.schedule from their email
@jobs_bp.route('/<uuid:id>/schedule', methods=['GET', 'POST'])
def schedule(id):
    job = org_get_or_404(Job, id)
    if not job.can_schedule and not job.can_reschedule:
        return not_at_step(job, 'scheduled')

    form = JobScheduleForm(due_by=job.due_by, scheduled_for=job.scheduled_for)

    if form.validate_on_submit():
        rescheduling = schedule_job(job, form.scheduled_for.data, g.person)
        db.session.commit()
        flash(f"Job '{job.description}' scheduled for {form.scheduled_for.data:%d-%m-%Y}.", 'success')
        if not rescheduling:
            tell_about_next_step(job, 'scheduled')
        return redirect(url_for('jobs.view', id=job.id))

    return render_template('jobs/schedule.html', job=job, form=form, on_behalf=not job.is_assignee(g.person))

# Start and Complete are for the job's assignee (require_job_permission), or someone with jobs.assign doing it
# for them. Start: the work has begun, now
@jobs_bp.route('/<uuid:id>/start', methods=['POST'])
def start(id):
    job = org_get_or_404(Job, id)
    if not job.can_start:
        return not_at_step(job, 'started')

    start_job(job)
    db.session.commit()
    flash(f"Job '{job.description}' started. Use Complete when the work's done.", 'success')
    tell_about_next_step(job, 'started')
    return redirect(url_for('jobs.view', id=job.id))

# Complete: when the work was done (UK time, not before it started) and what was done
@jobs_bp.route('/<uuid:id>/complete', methods=['GET', 'POST'])
def complete(id):
    job = org_get_or_404(Job, id)
    if not job.can_complete:
        return not_at_step(job, 'completed')

    form = JobCompleteForm(started_at=as_utc(job.started_at) if job.started_at else None)

    if form.validate_on_submit():
        complete_job(job, form.completed_at_uk(), form.completion_comments.data)
        db.session.commit()
        flash(f"Job '{job.description}' completed.", 'success')
        return redirect(url_for('jobs.view', id=job.id))

    return render_template('jobs/complete.html', job=job, form=form, on_behalf=not job.is_assignee(g.person))

def not_completed_warning(job):
    """Refuse to close a job until it's completed, so a closed job always has a record of the work."""
    flash(f"Job '{job.description}' can't be closed because it's {job.status_label.lower()}. "
          "It can be closed once it's completed.", 'warning')
    return redirect(url_for('jobs.view', id=job.id))

# the Close button: closes a completed job today. A planned job's schedule needs its next due date as well,
# so it goes to Update schedule, which closes the job and moves the schedule on in one commit
@jobs_bp.route('/<uuid:id>/close', methods=['POST'])
def close(id):
    job = org_get_or_404(Job, id)

    if job.status == 'closed':
        flash(f"Job '{job.description}' is already closed.", 'warning')
        return redirect(url_for('jobs.view', id=job.id))

    if job.status != 'completed':
        return not_completed_warning(job)

    if job.closes_with_schedule_update:
        return redirect(url_for('jobs.update_schedule', id=job.id))

    job.status = 'closed'
    job.closed_at = uk_today()
    job.closed_by = g.person
    db.session.commit()
    flash(f"Job '{job.description}' closed.", 'success')
    return redirect(url_for('jobs.view', id=job.id))

# the second step of closing a planned job: closes it today and moves its schedule on, together. The date
# offered is the date the work was completed (job.completed_on) plus the schedule's frequency, so the cycle
# follows the work, not the paperwork. It can be brought forward, but not past that or before today
@jobs_bp.route('/<uuid:id>/update-schedule', methods=['GET', 'POST'])
def update_schedule(id):
    job = org_get_or_404(Job, id)

    if job.status == 'closed' or not job.closes_with_schedule_update:
        flash(f"Job '{job.description}' has no planned job schedule waiting to be updated.", 'warning')
        return redirect(url_for('jobs.view', id=job.id))

    if job.status != 'completed':
        return not_completed_warning(job)

    planned_job_schedule = job.planned_job_schedule
    calculated = next_due_date(job.completed_on, planned_job_schedule.frequency)
    # a job completed long enough ago can give a date that's already passed. Today is then the only choice
    latest = max(calculated, uk_today())
    form = PlannedJobScheduleNextDueForm(latest=latest, data={'next_due_by': latest})

    if form.validate_on_submit():
        job.status = 'closed'
        job.closed_at = uk_today()
        job.closed_by = g.person
        planned_job_schedule.next_due_by = form.next_due_by.data
        planned_job_schedule.next_job_creation_date = job_creation_date(planned_job_schedule.next_due_by, planned_job_schedule.notice_days)
        db.session.commit()
        flash(f"Job '{job.description}' closed. Planned Job Schedule '{planned_job_schedule.description}' next due {planned_job_schedule.next_due_by:%d-%m-%Y}.", 'success')
        return redirect(url_for('jobs.view', id=job.id))

    return render_template('jobs/update_schedule.html', job=job, planned_job_schedule=planned_job_schedule, calculated=calculated, form=form)

@jobs_bp.route('/<uuid:id>/reopen', methods=['POST'])
def reopen(id):
    job = org_get_or_404(Job, id)

    if job.status not in JOB_FINISHED_STATUSES:
        flash(f"Job '{job.description}' isn't closed or rejected.", 'warning')
        return redirect(url_for('jobs.view', id=job.id))

    # closing or rejecting it moved its schedule on, so reopening would leave the schedule with two open jobs
    if job.source == 'planned':
        flash(f"Job '{job.description}' can't be reopened because it's a planned job. Create a new job instead.", 'warning')
        return redirect(url_for('jobs.view', id=job.id))

    # a rejected job goes back to new, to be approved or rejected again
    if job.status == 'rejected':
        job.status = 'new'
        job.rejected_at = None
        job.rejection_reason = None
    else:
        job.status = 'completed'
        job.closed_at = None
        job.closed_by = None
    db.session.commit()
    flash(f"Job '{job.description}' reopened.", 'success')
    return redirect(url_for('jobs.view', id=job.id))

### RELATED DATA ###

# jobs created as follow-up work for this one
@jobs_bp.route("/<uuid:id>/follow-up-jobs")
def follow_up_jobs(id):
    job = org_get_or_404(Job, id)
    pagination, search = related_page(related(job, Job.follow_up_jobs, Job), Job)
    return render_template("jobs/view/follow_up_jobs.html", job=job, jobs=pagination.items, pagination=pagination, search=search, active_tab="follow_up_jobs")

@jobs_bp.route("/<uuid:id>/assets")
def assets(id):
    job = org_get_or_404(Job, id)
    pagination, search = related_page(related(job, Job.assets, Asset), Asset)
    return render_template("jobs/view/assets.html", job=job, assets=pagination.items, pagination=pagination, search=search, active_tab="assets")

@jobs_bp.route("/<uuid:id>/assets/link", methods=['GET', 'POST'])
def link_assets(id):
    job = org_get_or_404(Job, id)

    # assets in the same top-level location (site) as the job that aren't already linked to it
    query = org_query(Asset).filter(
        same_top_location(Asset, job.location),
        ~Asset.jobs.any(Job.id == job.id)
    )

    if request.method == "POST":
        ids = selected_ids('asset_ids')

        if not ids:
            flash("Select at least one asset to link.", 'warning')
            return redirect(request.full_path)

        assets = query.filter(Asset.id.in_(ids)).all()
        job.assets.extend(assets)
        db.session.commit()
        flash(f"Linked {len(assets)} asset(s) to '{job.description}'.", 'success')
        return redirect(url_for('jobs.assets', id=job.id))

    pagination, search = related_page(query, Asset)
    return render_template("jobs/view/assets_link.html", job=job, assets=pagination.items, pagination=pagination, search=search)

@jobs_bp.route("/<uuid:id>/assets/unlink", methods=['GET', 'POST'])
def unlink_assets(id):
    job = org_get_or_404(Job, id)

    if request.method == "POST":
        ids = selected_ids('asset_ids')

        if not ids:
            flash("Select at least one asset to unlink.", 'warning')
            return redirect(request.full_path)

        assets = [asset for asset in job.assets if asset.id in ids]
        for asset in assets:
            job.assets.remove(asset)
        db.session.commit()
        flash(f"Unlinked {len(assets)} asset(s) from '{job.description}'.", 'success')
        return redirect(url_for('jobs.assets', id=job.id))

    pagination, search = related_page(related(job, Job.assets, Asset), Asset)
    return render_template("jobs/view/assets_unlink.html", job=job, assets=pagination.items, pagination=pagination, search=search)

@jobs_bp.route("/<uuid:id>/documents")
def documents(id):
    return documents_tab(Job, id, "jobs/view/documents.html", "job")
