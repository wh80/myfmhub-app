from flask import Blueprint, render_template, redirect, url_for, request, flash, g
from app import db
from app.forms import AssetForm
from app.models import Asset, Site, Job, JobSchedule
from app.routes.helpers import selected_ids, require_login, require_permission, org_query, org_get_or_404, parse_uuid, set_location_choices, check_location, location_options, filter_location
from app.services.exports import export_response
assets_bp = Blueprint('assets', __name__, url_prefix="/assets")
assets_bp.before_request(require_login)
assets_bp.before_request(require_permission)

# asset numbers and serial numbers are unique within the organisation, ignoring case
def value_taken(column, value, asset=None):
    if not value:
        return False
    query = org_query(Asset).filter(db.func.lower(column) == value.lower())
    if asset:
        query = query.filter(Asset.id != asset.id)
    return query.first() is not None

def check_unique(form, asset=None):
    ok = True
    if value_taken(Asset.asset_number, form.asset_number.data, asset):
        form.asset_number.errors.append('There is already an asset with this asset number.')
        ok = False
    if value_taken(Asset.serial_number, form.serial_number.data, asset):
        form.serial_number.errors.append('There is already an asset with this serial number.')
        ok = False
    return ok

# the index table's columns, for downloads. Keys match data-col in assets/asset_list.html
ASSET_COLUMNS = [
    ('description', 'Description', lambda asset: asset.description),
    ('site', 'Site', lambda asset: asset.site.name),
    ('building', 'Building', lambda asset: asset.building.name if asset.building else None),
    ('zone', 'Zone', lambda asset: asset.zone.name if asset.zone else None),
    ('asset_number', 'Asset Number', lambda asset: asset.asset_number),
    ('make', 'Make', lambda asset: asset.make),
    ('model', 'Model', lambda asset: asset.model),
    ('serial_number', 'Serial Number', lambda asset: asset.serial_number),
    ('purchase_date', 'Purchase Date', lambda asset: asset.purchase_date),
    ('purchased_from', 'Purchased From', lambda asset: asset.purchased_from),
    ('warranty_expiry', 'Warranty Expiry', lambda asset: asset.warranty_expiry),
    ('est_replacement_date', 'Est. Replacement Date', lambda asset: asset.estimated_replacement_date),
    ('est_replacement_cost', 'Est. Replacement Cost', lambda asset: asset.estimated_replacement_cost),
]

# the index filters, shared by the page and its downloads
def filtered_assets():
    search = request.args.get('search', '').strip()
    site_id = request.args.get('site_id', '').strip()

    query = org_query(Asset)

    # set by "View imported items" on an import
    import_id = parse_uuid(request.args.get('import_id'))
    if import_id:
        query = query.filter(Asset.import_event_id == import_id)

    if search:
        query = query.filter(
            db.or_(
                Asset.description.ilike(f'%{search}%'),
                Asset.asset_number.ilike(f'%{search}%'),
                Asset.make.ilike(f'%{search}%'),
                Asset.model.ilike(f'%{search}%'),
                Asset.serial_number.ilike(f'%{search}%')
            )
        )

    if site_id:
        query = query.filter(Asset.site_id == site_id)

    query, location = filter_location(query, Asset)

    return query.order_by(Asset.description, Asset.id), dict(search=search, site_id=site_id, import_id=import_id, **location)

@assets_bp.route('/')
def index():
    query, filters = filtered_assets()
    page = request.args.get('page', 1, type=int)

    pagination = query.paginate(page=page, per_page=25, error_out=False)

    return render_template(
        'assets/index.html',
        assets=pagination.items,
        pagination=pagination,
        **location_options(),
        **filters
    )

@assets_bp.route('/export/<any(csv, pdf):file_format>')
def export(file_format):
    query, _ = filtered_assets()
    assets = query.options(db.joinedload(Asset.site)).all()
    return export_response(file_format, 'assets', 'Assets', ASSET_COLUMNS, assets)

@assets_bp.route('/create', methods=['GET', 'POST'])
def create():
    form = AssetForm()
    set_location_choices(form)
    
    if form.validate_on_submit() and check_location(form) and check_unique(form):
        asset = Asset(
            description=form.description.data,
            asset_number=form.asset_number.data,
            make=form.make.data,
            model=form.model.data,
            serial_number=form.serial_number.data,
            warranty_expiry=form.warranty_expiry.data,
            purchase_date=form.purchase_date.data,
            purchased_from=form.purchased_from.data,
            estimated_replacement_date=form.estimated_replacement_date.data,
            estimated_replacement_cost=form.estimated_replacement_cost.data,
            site_id=form.site_id.data,
            building_id=parse_uuid(form.building_id.data),
            zone_id=parse_uuid(form.zone_id.data),
            organisation_id=g.user.organisation_id,
        )
        db.session.add(asset)
        db.session.commit()
        flash(f"Asset '{asset.description}' created successfully!", 'success')
        return redirect(url_for('assets.index'))

    return render_template('assets/create.html', form=form)

@assets_bp.route('/<uuid:id>', methods=['GET'])
def view(id):
    asset = org_get_or_404(Asset, id)

    return render_template('assets/view/overview.html', asset=asset, active_tab="overview")

@assets_bp.route('/<uuid:id>/edit', methods=['GET', 'POST'])
def edit(id):
    asset = org_get_or_404(Asset, id)

    form=AssetForm(obj=asset)
    set_location_choices(form)

    if form.validate_on_submit() and check_location(form) and check_unique(form, asset):

        asset.description=form.description.data
        asset.asset_number=form.asset_number.data
        asset.make=form.make.data
        asset.model=form.model.data
        asset.serial_number=form.serial_number.data
        asset.warranty_expiry=form.warranty_expiry.data
        asset.purchase_date=form.purchase_date.data
        asset.purchased_from=form.purchased_from.data
        asset.estimated_replacement_date=form.estimated_replacement_date.data
        asset.estimated_replacement_cost=form.estimated_replacement_cost.data
        asset.site_id=form.site_id.data
        asset.building_id=parse_uuid(form.building_id.data)
        asset.zone_id=parse_uuid(form.zone_id.data)
                
        db.session.commit()
        flash(f"Asset '{asset.description}' updated successfully!", 'success')
        return redirect(url_for('assets.view', id=asset.id))

    return render_template('assets/edit.html', asset=asset, form=form)

@assets_bp.route('/<uuid:id>/delete', methods=['GET', 'POST'])
def delete(id):
    asset = org_get_or_404(Asset, id)

    if request.method =="POST":
        db.session.delete(asset)
        db.session.commit()
        flash(f"Asset '{asset.description}' deleted successfully!", 'success')
        return redirect(url_for('assets.index'))

    return render_template('assets/delete.html', asset=asset)

### RELATED DATA ###

@assets_bp.route("/<uuid:id>/jobs")
def jobs(id):
    asset = org_get_or_404(Asset, id)
    return render_template("assets/view/jobs.html", asset=asset, active_tab="jobs",)

@assets_bp.route("/<uuid:id>/jobs/link", methods=['GET', 'POST'])
def link_jobs(id):
    asset = org_get_or_404(Asset, id)

    # jobs on the asset's site that aren't already linked to it
    query = org_query(Job).filter(
        Job.site_id == asset.site_id,
        ~Job.assets.any(Asset.id == asset.id)
    )

    if request.method == "POST":
        ids = selected_ids('job_ids')

        if not ids:
            flash("Select at least one job to link.", 'warning')
            return redirect(url_for('assets.link_jobs', id=asset.id))

        jobs = query.filter(Job.id.in_(ids)).all()
        asset.jobs.extend(jobs)
        db.session.commit()
        flash(f"Linked {len(jobs)} job(s) to '{asset.description}'.", 'success')
        return redirect(url_for('assets.jobs', id=asset.id))

    jobs = query.order_by(Job.description).all()
    return render_template("assets/view/jobs_link.html", asset=asset, jobs=jobs)

@assets_bp.route("/<uuid:id>/job-schedules")
def job_schedules(id):
    asset = org_get_or_404(Asset, id)
    return render_template("assets/view/job_schedules.html", asset=asset, active_tab="job_schedules",)

@assets_bp.route("/<uuid:id>/job-schedules/link", methods=['GET', 'POST'])
def link_job_schedules(id):
    asset = org_get_or_404(Asset, id)

    # job schedules on the asset's site that aren't already linked to it
    query = org_query(JobSchedule).filter(
        JobSchedule.site_id == asset.site_id,
        ~JobSchedule.assets.any(Asset.id == asset.id)
    )

    if request.method == "POST":
        ids = selected_ids('job_schedule_ids')

        if not ids:
            flash("Select at least one job schedule to link.", 'warning')
            return redirect(url_for('assets.link_job_schedules', id=asset.id))

        job_schedules = query.filter(JobSchedule.id.in_(ids)).all()
        asset.job_schedules.extend(job_schedules)
        db.session.commit()
        flash(f"Linked {len(job_schedules)} job schedule(s) to '{asset.description}'.", 'success')
        return redirect(url_for('assets.job_schedules', id=asset.id))

    job_schedules = query.order_by(JobSchedule.description).all()
    return render_template("assets/view/job_schedules_link.html", asset=asset, job_schedules=job_schedules)

@assets_bp.route("/<uuid:id>/jobs/unlink", methods=['GET', 'POST'])
def unlink_jobs(id):
    asset = org_get_or_404(Asset, id)

    if request.method == "POST":
        ids = selected_ids('job_ids')

        if not ids:
            flash("Select at least one job to unlink.", 'warning')
            return redirect(url_for('assets.unlink_jobs', id=asset.id))

        jobs = [job for job in asset.jobs if job.id in ids]
        for job in jobs:
            asset.jobs.remove(job)
        db.session.commit()
        flash(f"Unlinked {len(jobs)} job(s) from '{asset.description}'.", 'success')
        return redirect(url_for('assets.jobs', id=asset.id))

    jobs = sorted(asset.jobs, key=lambda job: job.description)
    return render_template("assets/view/jobs_unlink.html", asset=asset, jobs=jobs)

@assets_bp.route("/<uuid:id>/job-schedules/unlink", methods=['GET', 'POST'])
def unlink_job_schedules(id):
    asset = org_get_or_404(Asset, id)

    if request.method == "POST":
        ids = selected_ids('job_schedule_ids')

        if not ids:
            flash("Select at least one job schedule to unlink.", 'warning')
            return redirect(url_for('assets.unlink_job_schedules', id=asset.id))

        job_schedules = [job_schedule for job_schedule in asset.job_schedules if job_schedule.id in ids]
        for job_schedule in job_schedules:
            asset.job_schedules.remove(job_schedule)
        db.session.commit()
        flash(f"Unlinked {len(job_schedules)} job schedule(s) from '{asset.description}'.", 'success')
        return redirect(url_for('assets.job_schedules', id=asset.id))

    job_schedules = sorted(asset.job_schedules, key=lambda job_schedule: job_schedule.description)
    return render_template("assets/view/job_schedules_unlink.html", asset=asset, job_schedules=job_schedules)
