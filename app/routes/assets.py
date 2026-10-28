from flask import Blueprint, render_template, redirect, url_for, request, flash, g
from app import db
from app.forms import AssetForm, AssetAssignForm
from app.models import Asset, Site, Job, JobSchedule, Person, AssetCategory, AssetCondition
from app.routes.helpers import selected_ids, require_login, require_permission, org_query, org_get_or_404, parse_uuid, set_location_choices, check_location, location_options, filter_location, files_tab, related, related_page, set_reference_choices, reference_items, filter_reference
from app.services.reference_data import set_condition
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
    ('category', 'Asset Category', lambda asset: asset.asset_category.description if asset.asset_category else None),
    ('condition', 'Asset Condition', lambda asset: asset.asset_condition.description if asset.asset_condition else None),
    ('condition_set', 'Condition Set', lambda asset: asset.condition_set),
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
    query, asset_category_id = filter_reference(query, Asset, 'asset_category_id')
    query, asset_condition_id = filter_reference(query, Asset, 'asset_condition_id')

    return query.order_by(Asset.description, Asset.id), dict(search=search, site_id=site_id, import_id=import_id,
                                                             asset_category_id=asset_category_id, asset_condition_id=asset_condition_id, **location)

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
        asset_categories=reference_items(AssetCategory),
        asset_conditions=reference_items(AssetCondition),
        **filters
    )

@assets_bp.route('/export/<any(csv, pdf):file_format>')
def export(file_format):
    query, _ = filtered_assets()
    assets = query.options(db.joinedload(Asset.site)).all()
    return export_response(file_format, 'assets', 'Assets', ASSET_COLUMNS, assets)

@assets_bp.route('/create', methods=['GET', 'POST'])
def create():
    # ?parent_id= creates a sub-asset, from the parent's Sub-Assets tab. It starts at the parent's location
    parent = None
    if request.args.get('parent_id'):
        parent = org_get_or_404(Asset, parse_uuid(request.args['parent_id']))

    form = AssetForm()
    set_location_choices(form)
    set_reference_choices(form)
    if parent and request.method == 'GET':
        form.site_id.data = str(parent.site_id)
        form.building_id.data = str(parent.building_id or '')
        form.zone_id.data = str(parent.zone_id or '')

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
            asset_category_id=parse_uuid(form.asset_category_id.data),
            parent=parent,
            organisation_id=g.person.organisation_id,
        )
        set_condition(asset, 'asset_condition_id', parse_uuid(form.asset_condition_id.data))
        db.session.add(asset)
        db.session.commit()
        flash(f"Asset '{asset.description}' created successfully!", 'success')
        if parent:
            return redirect(url_for('assets.sub_assets', id=parent.id))
        return redirect(url_for('assets.index'))

    return render_template('assets/create.html', form=form, parent=parent)

@assets_bp.route('/<uuid:id>', methods=['GET'])
def view(id):
    asset = org_get_or_404(Asset, id)

    return render_template('assets/view/overview.html', asset=asset, active_tab="overview")

@assets_bp.route('/<uuid:id>/edit', methods=['GET', 'POST'])
def edit(id):
    asset = org_get_or_404(Asset, id)

    form=AssetForm(obj=asset)
    set_location_choices(form)
    set_reference_choices(form)

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
        asset.asset_category_id=parse_uuid(form.asset_category_id.data)
        # a changed condition sets condition_set to today
        set_condition(asset, 'asset_condition_id', parse_uuid(form.asset_condition_id.data))
                
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

# assign the asset to a person in the organisation, or to nobody. WTForms refuses anyone not in the choices
@assets_bp.route('/<uuid:id>/assign', methods=['GET', 'POST'])
def assign(id):
    asset = org_get_or_404(Asset, id)
    form = AssetAssignForm(data={'assigned_to_id': asset.assigned_to_id})
    people = org_query(Person).order_by(Person.first_name, Person.last_name, Person.id).all()
    form.assigned_to_id.choices = [('', '--- Unassigned ---')] + [(str(person.id), f'{person.first_name} {person.last_name} ({person.email})') for person in people]

    if form.validate_on_submit():
        asset.assigned_to_id = parse_uuid(form.assigned_to_id.data)
        db.session.commit()
        if asset.assigned_to:
            flash(f"Asset '{asset.description}' assigned to {asset.assigned_to.first_name} {asset.assigned_to.last_name}.", 'success')
        else:
            flash(f"Asset '{asset.description}' is no longer assigned to anyone.", 'success')
        return redirect(url_for('assets.view', id=asset.id))

    return render_template('assets/assign.html', asset=asset, form=form)

### RELATED DATA ###

@assets_bp.route("/<uuid:id>/sub-assets")
def sub_assets(id):
    asset = org_get_or_404(Asset, id)
    pagination, search = related_page(related(asset, Asset.sub_assets, Asset), Asset)
    return render_template("assets/view/sub_assets.html", asset=asset, assets=pagination.items, pagination=pagination, search=search, active_tab="sub_assets")

@assets_bp.route("/<uuid:id>/jobs")
def jobs(id):
    asset = org_get_or_404(Asset, id)
    pagination, search = related_page(related(asset, Asset.jobs, Job), Job)
    return render_template("assets/view/jobs.html", asset=asset, jobs=pagination.items, pagination=pagination, search=search, active_tab="jobs")

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
            return redirect(request.full_path)

        jobs = query.filter(Job.id.in_(ids)).all()
        asset.jobs.extend(jobs)
        db.session.commit()
        flash(f"Linked {len(jobs)} job(s) to '{asset.description}'.", 'success')
        return redirect(url_for('assets.jobs', id=asset.id))

    pagination, search = related_page(query, Job)
    return render_template("assets/view/jobs_link.html", asset=asset, jobs=pagination.items, pagination=pagination, search=search)

@assets_bp.route("/<uuid:id>/job-schedules")
def job_schedules(id):
    asset = org_get_or_404(Asset, id)
    pagination, search = related_page(related(asset, Asset.job_schedules, JobSchedule), JobSchedule)
    return render_template("assets/view/job_schedules.html", asset=asset, job_schedules=pagination.items, pagination=pagination, search=search, active_tab="job_schedules")

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
            return redirect(request.full_path)

        job_schedules = query.filter(JobSchedule.id.in_(ids)).all()
        asset.job_schedules.extend(job_schedules)
        db.session.commit()
        flash(f"Linked {len(job_schedules)} job schedule(s) to '{asset.description}'.", 'success')
        return redirect(url_for('assets.job_schedules', id=asset.id))

    pagination, search = related_page(query, JobSchedule)
    return render_template("assets/view/job_schedules_link.html", asset=asset, job_schedules=pagination.items, pagination=pagination, search=search)

@assets_bp.route("/<uuid:id>/jobs/unlink", methods=['GET', 'POST'])
def unlink_jobs(id):
    asset = org_get_or_404(Asset, id)

    if request.method == "POST":
        ids = selected_ids('job_ids')

        if not ids:
            flash("Select at least one job to unlink.", 'warning')
            return redirect(request.full_path)

        jobs = [job for job in asset.jobs if job.id in ids]
        for job in jobs:
            asset.jobs.remove(job)
        db.session.commit()
        flash(f"Unlinked {len(jobs)} job(s) from '{asset.description}'.", 'success')
        return redirect(url_for('assets.jobs', id=asset.id))

    pagination, search = related_page(related(asset, Asset.jobs, Job), Job)
    return render_template("assets/view/jobs_unlink.html", asset=asset, jobs=pagination.items, pagination=pagination, search=search)

@assets_bp.route("/<uuid:id>/job-schedules/unlink", methods=['GET', 'POST'])
def unlink_job_schedules(id):
    asset = org_get_or_404(Asset, id)

    if request.method == "POST":
        ids = selected_ids('job_schedule_ids')

        if not ids:
            flash("Select at least one job schedule to unlink.", 'warning')
            return redirect(request.full_path)

        job_schedules = [job_schedule for job_schedule in asset.job_schedules if job_schedule.id in ids]
        for job_schedule in job_schedules:
            asset.job_schedules.remove(job_schedule)
        db.session.commit()
        flash(f"Unlinked {len(job_schedules)} job schedule(s) from '{asset.description}'.", 'success')
        return redirect(url_for('assets.job_schedules', id=asset.id))

    pagination, search = related_page(related(asset, Asset.job_schedules, JobSchedule), JobSchedule)
    return render_template("assets/view/job_schedules_unlink.html", asset=asset, job_schedules=pagination.items, pagination=pagination, search=search)

@assets_bp.route("/<uuid:id>/files")
def files(id):
    return files_tab(Asset, id, "assets/view/files.html", "asset")
