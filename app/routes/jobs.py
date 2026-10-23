from flask import Blueprint, render_template, redirect, url_for, request, flash, g
from app import db
from app.forms import JobForm, JobCloseForm
from app.models import Job, Site, Asset, JOB_STATUSES, JOB_STATUS_LABELS, JOB_SOURCES, JOB_SOURCE_LABELS
from app.services.jobs import overdue_conditions
from app.services.scheduling import uk_today
from app.routes.helpers import selected_ids, parse_date, require_login, require_permission, org_query, org_get_or_404
from app.services.exports import export_response
jobs_bp = Blueprint('jobs', __name__, url_prefix="/jobs")
jobs_bp.before_request(require_login)
jobs_bp.before_request(require_permission)

# the index table's columns, for downloads. Keys match data-col in jobs/job_list.html
JOB_COLUMNS = [
    ('summary', 'Summary', lambda job: job.summary),
    ('site', 'Site', lambda job: job.site.name),
    ('due_by', 'Due By', lambda job: job.due_by),
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
                Job.summary.ilike(f'%{search}%'),
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
        query = query.filter(Job.due_by >= due_from)

    if due_to:
        query = query.filter(Job.due_by <= due_to)

    if closed_from:
        query = query.filter(Job.closed_at >= closed_from)

    if closed_to:
        query = query.filter(Job.closed_at <= closed_to)

    filters = dict(
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
    )
    return query.order_by(Job.summary, Job.id), filters

@jobs_bp.route('/')
def index():
    query, filters = filtered_jobs()
    page = request.args.get('page', 1, type=int)

    pagination = query.paginate(page=page, per_page=25, error_out=False)

    return render_template(
        'jobs/index.html',
        jobs=pagination.items,
        pagination=pagination,
        sites=org_query(Site).all(),
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
    form.site_id.choices = [
        ('', '--- Select Site ---')
    ] + [
        (str(s.id), s.name)
        for s in org_query(Site).order_by(Site.name).all()
    ]
    
    if form.validate_on_submit():
        job = Job(
            summary=form.summary.data,
            work_required=form.work_required.data,
            due_by=form.due_by.data,
            source='reactive',
            site_id=form.site_id.data,
            organisation_id=g.user.organisation_id,
        )
        db.session.add(job)
        db.session.commit()
        flash(f"Job '{job.summary}' created successfully!", 'success')
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
    form.site_id.choices = [(s.id, s.name) for s in org_query(Site).order_by(Site.name)]

    if form.validate_on_submit():
        
        job.summary=form.summary.data
        job.work_required=form.work_required.data
        job.due_by=form.due_by.data
        job.site_id=form.site_id.data
                
        db.session.commit()
        flash(f"Job '{job.work_required}' updated successfully!", 'success')
        return redirect(url_for('jobs.view', id=job.id))

    return render_template('jobs/edit.html', job=job, form=form)

@jobs_bp.route('/<uuid:id>/delete', methods=['GET', 'POST'])
def delete(id):
    job = org_get_or_404(Job, id)

    if request.method =="POST":
        db.session.delete(job)
        db.session.commit()
        flash(f"Job '{job.summary}' deleted successfully!", 'success')
        return redirect(url_for('jobs.index'))

    return render_template('jobs/delete.html', job=job)

@jobs_bp.route('/<uuid:id>/close', methods=['GET', 'POST'])
def close(id):
    job = org_get_or_404(Job, id)

    if job.status == 'closed':
        flash(f"Job '{job.summary}' is already closed.", 'warning')
        return redirect(url_for('jobs.view', id=job.id))

    form = JobCloseForm()

    if form.validate_on_submit():
        job.status = 'closed'
        job.closed_at = form.closed_at.data
        db.session.commit()
        flash(f"Job '{job.summary}' closed.", 'success')
        return redirect(url_for('jobs.view', id=job.id))

    return render_template('jobs/close.html', job=job, form=form)

@jobs_bp.route('/<uuid:id>/reopen', methods=['POST'])
def reopen(id):
    job = org_get_or_404(Job, id)

    if job.status != 'closed':
        flash(f"Job '{job.summary}' isn't closed.", 'warning')
        return redirect(url_for('jobs.view', id=job.id))

    job.status = 'open'
    job.closed_at = None
    db.session.commit()
    flash(f"Job '{job.summary}' reopened.", 'success')
    return redirect(url_for('jobs.view', id=job.id))

### RELATED DATA ###

@jobs_bp.route("/<uuid:id>/assets")
def assets(id):
    job = org_get_or_404(Job, id)
    return render_template("jobs/view/assets.html", job=job, active_tab="assets")

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
            return redirect(url_for('jobs.link_assets', id=job.id))

        assets = query.filter(Asset.id.in_(ids)).all()
        job.assets.extend(assets)
        db.session.commit()
        flash(f"Linked {len(assets)} asset(s) to '{job.summary}'.", 'success')
        return redirect(url_for('jobs.assets', id=job.id))

    assets = query.order_by(Asset.description).all()
    return render_template("jobs/view/assets_link.html", job=job, assets=assets)

@jobs_bp.route("/<uuid:id>/assets/unlink", methods=['GET', 'POST'])
def unlink_assets(id):
    job = org_get_or_404(Job, id)

    if request.method == "POST":
        ids = selected_ids('asset_ids')

        if not ids:
            flash("Select at least one asset to unlink.", 'warning')
            return redirect(url_for('jobs.unlink_assets', id=job.id))

        assets = [asset for asset in job.assets if asset.id in ids]
        for asset in assets:
            job.assets.remove(asset)
        db.session.commit()
        flash(f"Unlinked {len(assets)} asset(s) from '{job.summary}'.", 'success')
        return redirect(url_for('jobs.assets', id=job.id))

    assets = sorted(job.assets, key=lambda asset: asset.description)
    return render_template("jobs/view/assets_unlink.html", job=job, assets=assets)
