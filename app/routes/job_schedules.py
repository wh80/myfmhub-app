from flask import Blueprint, render_template, redirect, url_for, request, flash, g
from app import db
from app.forms import JobScheduleForm
from app.models import JobSchedule, Site, Asset
from app.routes.helpers import selected_ids, parse_date, require_login, require_permission, org_query, org_get_or_404, parse_uuid, set_location_choices, check_location, location_options, filter_location, files_tab
from app.services.exports import export_response
from app.services.permissions import can
from app.services.scheduling import job_creation_date, uk_today
job_schedules_bp = Blueprint('job_schedules', __name__, url_prefix="/job-schedules")
job_schedules_bp.before_request(require_login)
job_schedules_bp.before_request(require_permission)

# the index table's columns, for downloads. Keys match data-col in job_schedules/job_schedule_list.html
JOB_SCHEDULE_COLUMNS = [
    ('description', 'Description', lambda job_schedule: job_schedule.description),
    ('site', 'Site', lambda job_schedule: job_schedule.site.name),
    ('building', 'Building', lambda job_schedule: job_schedule.building.name if job_schedule.building else None),
    ('zone', 'Zone', lambda job_schedule: job_schedule.zone.name if job_schedule.zone else None),
    ('next_due', 'Next Due', lambda job_schedule: job_schedule.next_due),
    ('frequency', 'Frequency', lambda job_schedule: job_schedule.frequency_label),
    ('next_job_creation', 'Next Job Creation', lambda job_schedule: job_schedule.next_job_creation_label),
    ('statutory', 'Statutory', lambda job_schedule: job_schedule.statutory_compliance),
    ('active', 'Active', lambda job_schedule: job_schedule.is_active),
]

# making an inactive schedule active again needs a next due date in the future, so it doesn't
# start by raising a job that's already due. Adds the error to the form
def check_reactivation(form, job_schedule):
    if not job_schedule.is_active and form.is_active.data and form.next_due.data <= uk_today():
        form.next_due.errors.append('To make the schedule active again, the next due date must be in the future.')
        return False
    return True

# the index filters, shared by the page and its downloads
def filtered_job_schedules():
    search = request.args.get('search', '').strip()
    site_id = request.args.get('site_id', '').strip()
    statutory = request.args.get('statutory') == '1'
    next_due_from = parse_date(request.args.get('next_due_from'))
    next_due_to = parse_date(request.args.get('next_due_to'))
    # 'yes' or 'no'. Anything else shows both
    active = request.args.get('active', '').strip()
    if active not in ('yes', 'no'):
        active = ''

    query = org_query(JobSchedule)

    # set by "View imported items" on an import
    import_id = parse_uuid(request.args.get('import_id'))
    if import_id:
        query = query.filter(JobSchedule.import_event_id == import_id)

    if search:
        query = query.filter(
            db.or_(
                JobSchedule.description.ilike(f'%{search}%'),
                JobSchedule.work_required.ilike(f'%{search}%')
            )
        )

    if site_id:
        query = query.filter(JobSchedule.site_id == site_id)

    if statutory:
        query = query.filter(JobSchedule.statutory_compliance.is_(True))

    if active:
        query = query.filter(JobSchedule.is_active.is_(active == 'yes'))

    # the range includes both ends
    if next_due_from:
        query = query.filter(JobSchedule.next_due >= next_due_from)

    if next_due_to:
        query = query.filter(JobSchedule.next_due <= next_due_to)

    query, location = filter_location(query, JobSchedule)

    return query.order_by(JobSchedule.description, JobSchedule.id), dict(
        search=search, site_id=site_id, statutory=statutory, import_id=import_id,
        next_due_from=next_due_from, next_due_to=next_due_to, active=active, **location,
    )

@job_schedules_bp.route('/')
def index():
    query, filters = filtered_job_schedules()
    page = request.args.get('page', 1, type=int)

    pagination = query.paginate(page=page, per_page=25, error_out=False)

    return render_template(
        'job_schedules/index.html',
        job_schedules=pagination.items,
        pagination=pagination,
        **location_options(),
        **filters
    )

@job_schedules_bp.route('/export/<any(csv, pdf):file_format>')
def export(file_format):
    query, _ = filtered_job_schedules()
    job_schedules = query.options(db.joinedload(JobSchedule.site)).all()
    return export_response(file_format, 'job-schedules', 'Job Schedules', JOB_SCHEDULE_COLUMNS, job_schedules)

@job_schedules_bp.route('/create', methods=['GET', 'POST'])
def create():
    form = JobScheduleForm()
    set_location_choices(form)

    # Copy (?copy_from=<id>) pre-fills the scheduling fields. Next due, location and linked records
    # aren't copied (the due date should be chosen, not carried over), and the new schedule has no tie to the old one
    copy_from = None
    if request.args.get('copy_from'):
        copy_from = org_get_or_404(JobSchedule, parse_uuid(request.args['copy_from']))
        if request.method == 'GET':
            form.description.data = copy_from.description
            form.work_required.data = copy_from.work_required
            form.frequency.data = copy_from.frequency
            form.notice_days.data = copy_from.notice_days
            form.statutory_compliance.data = copy_from.statutory_compliance
            form.is_active.data = copy_from.is_active

    if form.validate_on_submit() and check_location(form):
        job_schedule = JobSchedule(
            description=form.description.data,
            work_required=form.work_required.data,
            next_due=form.next_due.data,
            frequency=form.frequency.data,
            notice_days=form.notice_days.data,
            next_job_creation_date=job_creation_date(form.next_due.data, form.notice_days.data),
            statutory_compliance=form.statutory_compliance.data,
            is_active=form.is_active.data,
            site_id=form.site_id.data,
            building_id=parse_uuid(form.building_id.data),
            zone_id=parse_uuid(form.zone_id.data),
            organisation_id=g.person.organisation_id,
        )
        db.session.add(job_schedule)
        db.session.commit()
        # offer to link assets straight away, if the user can (the page says it's saved)
        if can(g.person, 'job_schedules.edit'):
            return redirect(url_for('job_schedules.created', id=job_schedule.id))
        flash(f"Job Schedule '{job_schedule.description}' created successfully!", 'success')
        return redirect(url_for('job_schedules.index'))

    return render_template('job_schedules/create.html', form=form, copy_from=copy_from)

@job_schedules_bp.route('/<uuid:id>/created', methods=['GET'])
def created(id):
    job_schedule = org_get_or_404(JobSchedule, id)

    return render_template('job_schedules/created.html', job_schedule=job_schedule)

@job_schedules_bp.route('/<uuid:id>', methods=['GET'])
def view(id):
    job_schedule = org_get_or_404(JobSchedule, id)

    return render_template('job_schedules/view/overview.html', job_schedule=job_schedule, active_tab="overview")

@job_schedules_bp.route('/<uuid:id>/edit', methods=['GET', 'POST'])
def edit(id):
    job_schedule = org_get_or_404(JobSchedule, id)

    # changing the dates while a job is open could raise a second job and break the sequence.
    # Closing the job sets the next due date instead
    if job_schedule.open_job:
        flash(f"Job Schedule '{job_schedule.description}' can't be edited while it has an open job. Close the job first.", 'warning')
        return redirect(url_for('job_schedules.view', id=job_schedule.id))

    form=JobScheduleForm(obj=job_schedule)
    set_location_choices(form)

    if form.validate_on_submit() and check_location(form) and check_reactivation(form, job_schedule):
        
        job_schedule.description=form.description.data
        job_schedule.work_required=form.work_required.data
        job_schedule.next_due=form.next_due.data
        job_schedule.frequency=form.frequency.data
        job_schedule.notice_days=form.notice_days.data
        # there's no open job (checked above), so this also restarts a schedule whose open job was deleted
        job_schedule.next_job_creation_date=job_creation_date(job_schedule.next_due, job_schedule.notice_days)
        job_schedule.statutory_compliance=form.statutory_compliance.data
        job_schedule.is_active=form.is_active.data
        job_schedule.site_id=form.site_id.data
        job_schedule.building_id=parse_uuid(form.building_id.data)
        job_schedule.zone_id=parse_uuid(form.zone_id.data)
                
        db.session.commit()
        flash(f"Job Schedule '{job_schedule.work_required}' updated successfully!", 'success')
        return redirect(url_for('job_schedules.view', id=job_schedule.id))

    return render_template('job_schedules/edit.html', job_schedule=job_schedule, form=form)

@job_schedules_bp.route('/<uuid:id>/delete', methods=['GET', 'POST'])
def delete(id):
    job_schedule = org_get_or_404(JobSchedule, id)

    # its jobs would lose their link to it, and with it the history of the sequence
    if job_schedule.jobs:
        flash(f"Job Schedule '{job_schedule.description}' can't be deleted because it has raised jobs.", 'warning')
        return redirect(url_for('job_schedules.view', id=job_schedule.id))

    if request.method =="POST":
        db.session.delete(job_schedule)
        db.session.commit()
        flash(f"Job Schedule '{job_schedule.description}' deleted successfully!", 'success')
        return redirect(url_for('job_schedules.index'))

    return render_template('job_schedules/delete.html', job_schedule=job_schedule)


### RELATED DATA ###

@job_schedules_bp.route("/<uuid:id>/jobs")
def jobs(id):
    job_schedule = org_get_or_404(JobSchedule, id)
    return render_template("job_schedules/view/jobs.html", job_schedule=job_schedule, active_tab="jobs",)

@job_schedules_bp.route("/<uuid:id>/assets")
def assets(id):
    job_schedule = org_get_or_404(JobSchedule, id)
    return render_template("job_schedules/view/assets.html", job_schedule=job_schedule, active_tab="assets",)

@job_schedules_bp.route("/<uuid:id>/assets/link", methods=['GET', 'POST'])
def link_assets(id):
    job_schedule = org_get_or_404(JobSchedule, id)

    # assets on the job schedule's site that aren't already linked to it
    query = org_query(Asset).filter(
        Asset.site_id == job_schedule.site_id,
        ~Asset.job_schedules.any(JobSchedule.id == job_schedule.id)
    )

    if request.method == "POST":
        ids = selected_ids('asset_ids')

        if not ids:
            flash("Select at least one asset to link.", 'warning')
            return redirect(url_for('job_schedules.link_assets', id=job_schedule.id))

        assets = query.filter(Asset.id.in_(ids)).all()
        job_schedule.assets.extend(assets)
        db.session.commit()
        flash(f"Linked {len(assets)} asset(s) to '{job_schedule.description}'.", 'success')
        return redirect(url_for('job_schedules.assets', id=job_schedule.id))

    assets = query.order_by(Asset.description).all()
    return render_template("job_schedules/view/assets_link.html", job_schedule=job_schedule, assets=assets)

@job_schedules_bp.route("/<uuid:id>/assets/unlink", methods=['GET', 'POST'])
def unlink_assets(id):
    job_schedule = org_get_or_404(JobSchedule, id)

    if request.method == "POST":
        ids = selected_ids('asset_ids')

        if not ids:
            flash("Select at least one asset to unlink.", 'warning')
            return redirect(url_for('job_schedules.unlink_assets', id=job_schedule.id))

        assets = [asset for asset in job_schedule.assets if asset.id in ids]
        for asset in assets:
            job_schedule.assets.remove(asset)
        db.session.commit()
        flash(f"Unlinked {len(assets)} asset(s) from '{job_schedule.description}'.", 'success')
        return redirect(url_for('job_schedules.assets', id=job_schedule.id))

    assets = sorted(job_schedule.assets, key=lambda asset: asset.description)
    return render_template("job_schedules/view/assets_unlink.html", job_schedule=job_schedule, assets=assets)

@job_schedules_bp.route("/<uuid:id>/files")
def files(id):
    return files_tab(JobSchedule, id, "job_schedules/view/files.html", "job_schedule")
