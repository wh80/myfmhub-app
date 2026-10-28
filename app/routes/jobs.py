from flask import Blueprint, render_template, redirect, url_for, request, flash, g
from app import db
from app.forms import JobForm, JobCloseForm, JobScheduleNextDueForm
from app.models import Job, Site, Asset, JobCategory, JOB_STATUSES, JOB_STATUS_LABELS, JOB_SOURCES, JOB_SOURCE_LABELS
from app.services.jobs import overdue_conditions
from app.services.scheduling import job_creation_date, next_due_date, uk_today
from app.routes.helpers import selected_ids, parse_date, require_login, require_permission, org_query, org_get_or_404, parse_uuid, set_location_choices, check_location, location_options, filter_location, files_tab, related, related_page, set_reference_choices, reference_items, filter_reference
from app.services.exports import export_response
jobs_bp = Blueprint('jobs', __name__, url_prefix="/jobs")
jobs_bp.before_request(require_login)
jobs_bp.before_request(require_permission)

# the index table's columns, for downloads. Keys match data-col in jobs/job_list.html
JOB_COLUMNS = [
    ('description', 'Description', lambda job: job.description),
    ('site', 'Site', lambda job: job.site.name),
    ('building', 'Building', lambda job: job.building.name if job.building else None),
    ('zone', 'Zone', lambda job: job.zone.name if job.zone else None),
    ('category', 'Job Category', lambda job: job.job_category.description if job.job_category else None),
    ('due', 'Due', lambda job: job.due),
    ('source', 'Source', lambda job: job.source_label),
    ('status', 'Status', lambda job: job.status_label),
    ('closed_on', 'Closed On', lambda job: job.closed_at),
    ('statutory', 'Statutory', lambda job: job.statutory_compliance),
]

# the index filters, shared by the page and its downloads
def filtered_jobs():
    search = request.args.get('search', '').strip()
    site_id = request.args.get('site_id', '').strip()
    statutory = request.args.get('statutory') == '1'
    overdue = request.args.get('overdue') == '1'
    # unknown values are ignored rather than matching nothing.
    # 'not_closed' is a filter-only option for new and open together
    status = request.args.get('status', '').strip()
    status = status if status in JOB_STATUS_LABELS or status == 'not_closed' else ''
    source = request.args.get('source', '').strip()
    source = source if source in JOB_SOURCE_LABELS else ''
    due_from = parse_date(request.args.get('due_from'))
    due_to = parse_date(request.args.get('due_to'))
    closed_from = parse_date(request.args.get('closed_from'))
    closed_to = parse_date(request.args.get('closed_to'))

    query = org_query(Job)

    if search:
        query = query.filter(
            db.or_(
                Job.description.ilike(f'%{search}%'),
                Job.work_required.ilike(f'%{search}%')
            )
        )

    if site_id:
        query = query.filter(Job.site_id == site_id)

    if statutory:
        query = query.filter(Job.statutory_compliance.is_(True))

    if overdue:
        query = query.filter(*overdue_conditions(uk_today()))

    if status == 'not_closed':
        query = query.filter(Job.status != 'closed')
    elif status:
        query = query.filter(Job.status == status)

    if source:
        query = query.filter(Job.source == source)

    # date ranges include both ends. Jobs with no date drop out once either end is set
    if due_from:
        query = query.filter(Job.due >= due_from)

    if due_to:
        query = query.filter(Job.due <= due_to)

    if closed_from:
        query = query.filter(Job.closed_at >= closed_from)

    if closed_to:
        query = query.filter(Job.closed_at <= closed_to)

    query, location = filter_location(query, Job)
    query, job_category_id = filter_reference(query, Job, 'job_category_id')

    filters = dict(
        job_category_id=job_category_id,
        search=search,
        site_id=site_id,
        statutory=statutory,
        overdue=overdue,
        status=status,
        source=source,
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

    return render_template(
        'jobs/index.html',
        jobs=pagination.items,
        pagination=pagination,
        **location_options(),
        job_categories=reference_items(JobCategory),
        statuses=JOB_STATUSES,
        sources=JOB_SOURCES,
        **filters
    )

@jobs_bp.route('/export/<any(csv, pdf):file_format>')
def export(file_format):
    query, _ = filtered_jobs()
    jobs = query.options(db.joinedload(Job.site)).all()
    return export_response(file_format, 'jobs', 'Jobs', JOB_COLUMNS, jobs)

@jobs_bp.route('/create', methods=['GET', 'POST'])
def create():
    form = JobForm()
    set_location_choices(form)
    set_reference_choices(form)
    
    if form.validate_on_submit() and check_location(form):
        job = Job(
            description=form.description.data,
            work_required=form.work_required.data,
            due=form.due.data,
            source='reactive',
            site_id=form.site_id.data,
            building_id=parse_uuid(form.building_id.data),
            zone_id=parse_uuid(form.zone_id.data),
            job_category_id=parse_uuid(form.job_category_id.data),
            organisation_id=g.person.organisation_id,
        )
        db.session.add(job)
        db.session.commit()
        flash(f"Job '{job.description}' created successfully!", 'success')
        return redirect(url_for('jobs.index'))

    return render_template('jobs/create.html', form=form)

@jobs_bp.route('/<uuid:id>', methods=['GET'])
def view(id):
    job = org_get_or_404(Job, id)

    return render_template('jobs/view/overview.html', job=job, active_tab="overview")

@jobs_bp.route('/<uuid:id>/edit', methods=['GET', 'POST'])
def edit(id):
    job = org_get_or_404(Job, id)

    form=JobForm(obj=job)
    set_location_choices(form)
    set_reference_choices(form)

    if form.validate_on_submit() and check_location(form):
        
        job.description=form.description.data
        job.work_required=form.work_required.data
        # a planned job is due when its schedule said, so its due date can't be changed
        if job.source != 'planned':
            job.due=form.due.data
        job.site_id=form.site_id.data
        job.building_id=parse_uuid(form.building_id.data)
        job.zone_id=parse_uuid(form.zone_id.data)
        job.job_category_id=parse_uuid(form.job_category_id.data)
                
        db.session.commit()
        flash(f"Job '{job.work_required}' updated successfully!", 'success')
        return redirect(url_for('jobs.view', id=job.id))

    return render_template('jobs/edit.html', job=job, form=form)

@jobs_bp.route('/<uuid:id>/delete', methods=['GET', 'POST'])
def delete(id):
    job = org_get_or_404(Job, id)

    # its schedule is waiting for it to close, so deleting it would stop the schedule raising jobs
    if job.source == 'planned' and job.status != 'closed':
        flash(f"Job '{job.description}' can't be deleted because it's an open planned job. Close it instead.", 'warning')
        return redirect(url_for('jobs.view', id=job.id))

    if request.method =="POST":
        db.session.delete(job)
        db.session.commit()
        flash(f"Job '{job.description}' deleted successfully!", 'success')
        return redirect(url_for('jobs.index'))

    return render_template('jobs/delete.html', job=job)

@jobs_bp.route('/<uuid:id>/close', methods=['GET', 'POST'])
def close(id):
    job = org_get_or_404(Job, id)

    if job.status == 'closed':
        flash(f"Job '{job.description}' is already closed.", 'warning')
        return redirect(url_for('jobs.view', id=job.id))

    # ?closed_at= is set by Back on the update schedule page, so the date chosen is kept
    form = JobCloseForm(raised_on=job.raised_on, closed_at=parse_date(request.args.get('closed_at')) or uk_today())

    if form.validate_on_submit():
        # a planned job's schedule needs its next due date as well. Nothing is saved here: the update
        # schedule page closes the job and moves the schedule on in one commit, so they can't get out of step
        if job.closes_with_schedule_update:
            return redirect(url_for('jobs.update_schedule', id=job.id, closed_at=form.closed_at.data.isoformat()))
        job.status = 'closed'
        job.closed_at = form.closed_at.data
        db.session.commit()
        flash(f"Job '{job.description}' closed.", 'success')
        return redirect(url_for('jobs.view', id=job.id))

    return render_template('jobs/close.html', job=job, form=form)

# the second step of closing a planned job: closes it with the close date from the close page
# (?closed_at=) and moves its schedule on, together. The date offered is the close date plus the
# schedule's frequency. It can be brought forward, but not past that or before today
@jobs_bp.route('/<uuid:id>/update-schedule', methods=['GET', 'POST'])
def update_schedule(id):
    job = org_get_or_404(Job, id)

    if job.status == 'closed' or not job.closes_with_schedule_update:
        flash(f"Job '{job.description}' has no job schedule waiting to be updated.", 'warning')
        return redirect(url_for('jobs.view', id=job.id))

    # checked again, as the close page did, because it comes from the query string
    closed_at = parse_date(request.args.get('closed_at'))
    if closed_at is None or closed_at > uk_today() or closed_at < job.raised_on:
        flash('Choose the date the job was closed.', 'warning')
        return redirect(url_for('jobs.close', id=job.id))

    job_schedule = job.job_schedule
    calculated = next_due_date(closed_at, job_schedule.frequency)
    # a job closed long enough ago can give a date that's already passed. Today is then the only choice
    latest = max(calculated, uk_today())
    form = JobScheduleNextDueForm(latest=latest, data={'next_due': latest})

    if form.validate_on_submit():
        job.status = 'closed'
        job.closed_at = closed_at
        job_schedule.next_due = form.next_due.data
        job_schedule.next_job_creation_date = job_creation_date(job_schedule.next_due, job_schedule.notice_days)
        db.session.commit()
        flash(f"Job '{job.description}' closed. Job Schedule '{job_schedule.description}' next due {job_schedule.next_due:%d-%m-%Y}.", 'success')
        return redirect(url_for('jobs.view', id=job.id))

    return render_template('jobs/update_schedule.html', job=job, job_schedule=job_schedule, closed_at=closed_at, calculated=calculated, form=form)

@jobs_bp.route('/<uuid:id>/reopen', methods=['POST'])
def reopen(id):
    job = org_get_or_404(Job, id)

    if job.status != 'closed':
        flash(f"Job '{job.description}' isn't closed.", 'warning')
        return redirect(url_for('jobs.view', id=job.id))

    # closing it moved its schedule on, so reopening would leave the schedule with two open jobs
    if job.source == 'planned':
        flash(f"Job '{job.description}' can't be reopened because it's a planned job. Create a new job instead.", 'warning')
        return redirect(url_for('jobs.view', id=job.id))

    job.status = 'open'
    job.closed_at = None
    db.session.commit()
    flash(f"Job '{job.description}' reopened.", 'success')
    return redirect(url_for('jobs.view', id=job.id))

### RELATED DATA ###

@jobs_bp.route("/<uuid:id>/assets")
def assets(id):
    job = org_get_or_404(Job, id)
    pagination, search = related_page(related(job, Job.assets, Asset), Asset)
    return render_template("jobs/view/assets.html", job=job, assets=pagination.items, pagination=pagination, search=search, active_tab="assets")

@jobs_bp.route("/<uuid:id>/assets/link", methods=['GET', 'POST'])
def link_assets(id):
    job = org_get_or_404(Job, id)

    # assets on the job's site that aren't already linked to it
    query = org_query(Asset).filter(
        Asset.site_id == job.site_id,
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

@jobs_bp.route("/<uuid:id>/files")
def files(id):
    return files_tab(Job, id, "jobs/view/files.html", "job")
