from flask import Blueprint, render_template, redirect, url_for, request, flash, g
from app import db
from app.forms import PlannedJobScheduleForm
from app.models import PlannedJobSchedule, Asset, Job, JobCategory
from app.routes.helpers import selected_ids, parse_date, require_login, require_permission, org_query, org_get_or_404, parse_uuid, set_location_choices, create_from, location_options, filter_location, same_top_location, documents_tab, related, related_page, set_reference_choices, reference_items, filter_reference
from app.services.exports import export_response
from app.services.permissions import can
from app.services.scheduling import job_creation_date, uk_today
planned_jobs_bp = Blueprint('planned_jobs', __name__, url_prefix="/planned-jobs")
planned_jobs_bp.before_request(require_login)
planned_jobs_bp.before_request(require_permission)

# the index table's columns, for downloads. Keys match data-col in planned_jobs/planned_job_schedule_list.html
PLANNED_JOB_COLUMNS = [
    ('description', 'Description', lambda planned_job_schedule: planned_job_schedule.description),
    ('location', 'Location', lambda planned_job_schedule: planned_job_schedule.location.materialised_path),
    ('category', 'Job Category', lambda planned_job_schedule: planned_job_schedule.job_category.description if planned_job_schedule.job_category else None),
    ('next_due_by', 'Next Due By', lambda planned_job_schedule: planned_job_schedule.next_due_by),
    ('frequency', 'Frequency', lambda planned_job_schedule: planned_job_schedule.frequency_label),
    ('next_job_creation', 'Next Job Creation', lambda planned_job_schedule: planned_job_schedule.next_job_creation_label),
    ('statutory', 'Statutory', lambda planned_job_schedule: planned_job_schedule.statutory_compliance),
    ('active', 'Active', lambda planned_job_schedule: planned_job_schedule.is_active),
]

# making an inactive schedule active again needs a next due date in the future, so it doesn't
# start by raising a job that's already due. Adds the error to the form
def check_reactivation(form, planned_job_schedule):
    if not planned_job_schedule.is_active and form.is_active.data and form.next_due_by.data <= uk_today():
        form.next_due_by.errors.append('To make the schedule active again, the next due date must be in the future.')
        return False
    return True

# the index filters, shared by the page and its downloads
def filtered_planned_jobs():
    search = request.args.get('search', '').strip()
    statutory = request.args.get('statutory') == '1'
    next_due_from = parse_date(request.args.get('next_due_from'))
    next_due_to = parse_date(request.args.get('next_due_to'))
    # 'yes' or 'no'. Anything else shows both
    active = request.args.get('active', '').strip()
    if active not in ('yes', 'no'):
        active = ''

    query = org_query(PlannedJobSchedule)

    # set by "View imported items" on an import
    import_id = parse_uuid(request.args.get('import_id'))
    if import_id:
        query = query.filter(PlannedJobSchedule.import_event_id == import_id)

    if search:
        query = query.filter(
            db.or_(
                PlannedJobSchedule.description.ilike(f'%{search}%'),
                PlannedJobSchedule.work_required.ilike(f'%{search}%')
            )
        )

    if statutory:
        query = query.filter(PlannedJobSchedule.statutory_compliance.is_(True))

    if active:
        query = query.filter(PlannedJobSchedule.is_active.is_(active == 'yes'))

    # the range includes both ends
    if next_due_from:
        query = query.filter(PlannedJobSchedule.next_due_by >= next_due_from)

    if next_due_to:
        query = query.filter(PlannedJobSchedule.next_due_by <= next_due_to)

    query, location = filter_location(query, PlannedJobSchedule)
    query, job_category_id = filter_reference(query, PlannedJobSchedule, 'job_category_id')

    return query.order_by(PlannedJobSchedule.description, PlannedJobSchedule.id), dict(
        job_category_id=job_category_id, search=search, statutory=statutory, import_id=import_id,
        next_due_from=next_due_from, next_due_to=next_due_to, active=active, **location,
    )

@planned_jobs_bp.route('/')
def index():
    query, filters = filtered_planned_jobs()
    page = request.args.get('page', 1, type=int)

    pagination = query.paginate(page=page, per_page=25, error_out=False)

    return render_template(
        'planned_jobs/index.html',
        planned_job_schedules=pagination.items,
        pagination=pagination,
        **location_options(),
        job_categories=reference_items(JobCategory),
        **filters
    )

@planned_jobs_bp.route('/export/<any(xlsx, pdf):file_format>')
def export(file_format):
    query, _ = filtered_planned_jobs()
    planned_job_schedules = query.options(db.joinedload(PlannedJobSchedule.location)).all()
    return export_response(file_format, 'planned-jobs', 'Planned Jobs', PLANNED_JOB_COLUMNS, planned_job_schedules)

@planned_jobs_bp.route('/create', methods=['GET', 'POST'])
def create():
    # ?asset_id= (an asset's Planned Jobs tab) links the new schedule to the asset, and ?location_id= (a
    # location's tab) puts it there. Either fixes the location: the select is hidden and what's posted is ignored
    asset, fixed_location = create_from()

    form = PlannedJobScheduleForm()
    set_location_choices(form)
    set_reference_choices(form)
    if fixed_location:
        form.location_id.data = str(fixed_location.id)

    # Copy (?copy_from=<id>) pre-fills the scheduling fields. Next due, location and linked records
    # aren't copied (the due date should be chosen, not carried over), and the new schedule has no tie to the old one
    copy_from = None
    if request.args.get('copy_from'):
        copy_from = org_get_or_404(PlannedJobSchedule, parse_uuid(request.args['copy_from']))
        if request.method == 'GET':
            form.description.data = copy_from.description
            form.work_required.data = copy_from.work_required
            form.frequency.data = copy_from.frequency
            form.notice_days.data = copy_from.notice_days
            form.statutory_compliance.data = copy_from.statutory_compliance
            form.is_active.data = copy_from.is_active
            form.job_category_id.data = str(copy_from.job_category_id) if copy_from.job_category_id else ''
            form.estimated_cost.data = copy_from.estimated_cost
            form.estimated_duration.data = copy_from.estimated_duration

    if form.validate_on_submit():
        planned_job_schedule = PlannedJobSchedule(
            description=form.description.data,
            work_required=form.work_required.data,
            next_due_by=form.next_due_by.data,
            frequency=form.frequency.data,
            notice_days=form.notice_days.data,
            next_job_creation_date=job_creation_date(form.next_due_by.data, form.notice_days.data),
            statutory_compliance=form.statutory_compliance.data,
            is_active=form.is_active.data,
            location_id=parse_uuid(form.location_id.data),
            job_category_id=parse_uuid(form.job_category_id.data),
            estimated_cost=form.estimated_cost.data,
            estimated_duration=form.estimated_duration.data,
            organisation_id=g.person.organisation_id,
        )
        if asset:
            planned_job_schedule.assets.append(asset)
        db.session.add(planned_job_schedule)
        db.session.commit()
        # back to the tab it was created from
        if asset or fixed_location:
            flash(f"Planned Job Schedule '{planned_job_schedule.description}' created successfully!", 'success')
            if asset:
                return redirect(url_for('assets.planned_jobs', id=asset.id))
            return redirect(url_for('locations.planned_jobs', id=fixed_location.id))
        # offer to link assets straight away, if the user can (the page says it's saved)
        if can(g.person, 'planned_jobs.edit'):
            return redirect(url_for('planned_jobs.created', id=planned_job_schedule.id))
        flash(f"Planned Job Schedule '{planned_job_schedule.description}' created successfully!", 'success')
        return redirect(url_for('planned_jobs.index'))

    return render_template('planned_jobs/create.html', form=form, copy_from=copy_from, from_asset=asset, fixed_location=fixed_location)

@planned_jobs_bp.route('/<uuid:id>/created', methods=['GET'])
def created(id):
    planned_job_schedule = org_get_or_404(PlannedJobSchedule, id)

    return render_template('planned_jobs/created.html', planned_job_schedule=planned_job_schedule)

@planned_jobs_bp.route('/<uuid:id>', methods=['GET'])
def view(id):
    planned_job_schedule = org_get_or_404(PlannedJobSchedule, id)

    return render_template('planned_jobs/view/overview.html', planned_job_schedule=planned_job_schedule, active_tab="overview")

@planned_jobs_bp.route('/<uuid:id>/edit', methods=['GET', 'POST'])
def edit(id):
    planned_job_schedule = org_get_or_404(PlannedJobSchedule, id)

    # changing the dates while a job is open could raise a second job and break the sequence.
    # Closing the job sets the next due date instead
    if planned_job_schedule.open_job:
        flash(f"Planned Job Schedule '{planned_job_schedule.description}' can't be edited while it has an open job. Close the job first.", 'warning')
        return redirect(url_for('planned_jobs.view', id=planned_job_schedule.id))

    form=PlannedJobScheduleForm(obj=planned_job_schedule)
    set_location_choices(form)
    set_reference_choices(form)

    if form.validate_on_submit() and check_reactivation(form, planned_job_schedule):
        
        planned_job_schedule.description=form.description.data
        planned_job_schedule.work_required=form.work_required.data
        planned_job_schedule.next_due_by=form.next_due_by.data
        planned_job_schedule.frequency=form.frequency.data
        planned_job_schedule.notice_days=form.notice_days.data
        # there's no open job (checked above), so this also restarts a schedule whose open job was deleted
        planned_job_schedule.next_job_creation_date=job_creation_date(planned_job_schedule.next_due_by, planned_job_schedule.notice_days)
        planned_job_schedule.statutory_compliance=form.statutory_compliance.data
        planned_job_schedule.is_active=form.is_active.data
        planned_job_schedule.location_id=parse_uuid(form.location_id.data)
        planned_job_schedule.job_category_id=parse_uuid(form.job_category_id.data)
        planned_job_schedule.estimated_cost=form.estimated_cost.data
        planned_job_schedule.estimated_duration=form.estimated_duration.data
                
        db.session.commit()
        flash(f"Planned Job Schedule '{planned_job_schedule.work_required}' updated successfully!", 'success')
        return redirect(url_for('planned_jobs.view', id=planned_job_schedule.id))

    return render_template('planned_jobs/edit.html', planned_job_schedule=planned_job_schedule, form=form)

@planned_jobs_bp.route('/<uuid:id>/delete', methods=['GET', 'POST'])
def delete(id):
    planned_job_schedule = org_get_or_404(PlannedJobSchedule, id)

    # its jobs would lose their link to it, and with it the history of the sequence
    if planned_job_schedule.jobs:
        flash(f"Planned Job Schedule '{planned_job_schedule.description}' can't be deleted because it has raised jobs.", 'warning')
        return redirect(url_for('planned_jobs.view', id=planned_job_schedule.id))

    if request.method =="POST":
        db.session.delete(planned_job_schedule)
        db.session.commit()
        flash(f"Planned Job Schedule '{planned_job_schedule.description}' deleted successfully!", 'success')
        return redirect(url_for('planned_jobs.index'))

    return render_template('planned_jobs/delete.html', planned_job_schedule=planned_job_schedule)


### RELATED DATA ###

@planned_jobs_bp.route("/<uuid:id>/jobs")
def jobs(id):
    planned_job_schedule = org_get_or_404(PlannedJobSchedule, id)
    pagination, search = related_page(related(planned_job_schedule, PlannedJobSchedule.jobs, Job), Job)
    return render_template("planned_jobs/view/jobs.html", planned_job_schedule=planned_job_schedule, jobs=pagination.items, pagination=pagination, search=search, active_tab="jobs")

@planned_jobs_bp.route("/<uuid:id>/assets")
def assets(id):
    planned_job_schedule = org_get_or_404(PlannedJobSchedule, id)
    pagination, search = related_page(related(planned_job_schedule, PlannedJobSchedule.assets, Asset), Asset)
    return render_template("planned_jobs/view/assets.html", planned_job_schedule=planned_job_schedule, assets=pagination.items, pagination=pagination, search=search, active_tab="assets")

@planned_jobs_bp.route("/<uuid:id>/assets/link", methods=['GET', 'POST'])
def link_assets(id):
    planned_job_schedule = org_get_or_404(PlannedJobSchedule, id)

    # assets in the same top-level location (site) as the planned job schedule that aren't already linked to it
    query = org_query(Asset).filter(
        same_top_location(Asset, planned_job_schedule.location),
        ~Asset.planned_job_schedules.any(PlannedJobSchedule.id == planned_job_schedule.id)
    )

    if request.method == "POST":
        ids = selected_ids('asset_ids')

        if not ids:
            flash("Select at least one asset to link.", 'warning')
            return redirect(request.full_path)

        assets = query.filter(Asset.id.in_(ids)).all()
        planned_job_schedule.assets.extend(assets)
        db.session.commit()
        flash(f"Linked {len(assets)} asset(s) to '{planned_job_schedule.description}'.", 'success')
        return redirect(url_for('planned_jobs.assets', id=planned_job_schedule.id))

    pagination, search = related_page(query, Asset)
    return render_template("planned_jobs/view/assets_link.html", planned_job_schedule=planned_job_schedule, assets=pagination.items, pagination=pagination, search=search)

@planned_jobs_bp.route("/<uuid:id>/assets/unlink", methods=['GET', 'POST'])
def unlink_assets(id):
    planned_job_schedule = org_get_or_404(PlannedJobSchedule, id)

    if request.method == "POST":
        ids = selected_ids('asset_ids')

        if not ids:
            flash("Select at least one asset to unlink.", 'warning')
            return redirect(request.full_path)

        assets = [asset for asset in planned_job_schedule.assets if asset.id in ids]
        for asset in assets:
            planned_job_schedule.assets.remove(asset)
        db.session.commit()
        flash(f"Unlinked {len(assets)} asset(s) from '{planned_job_schedule.description}'.", 'success')
        return redirect(url_for('planned_jobs.assets', id=planned_job_schedule.id))

    pagination, search = related_page(related(planned_job_schedule, PlannedJobSchedule.assets, Asset), Asset)
    return render_template("planned_jobs/view/assets_unlink.html", planned_job_schedule=planned_job_schedule, assets=pagination.items, pagination=pagination, search=search)

@planned_jobs_bp.route("/<uuid:id>/documents")
def documents(id):
    return documents_tab(PlannedJobSchedule, id, "planned_jobs/view/documents.html", "planned_job_schedule")
