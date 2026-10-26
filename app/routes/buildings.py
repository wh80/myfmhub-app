from flask import Blueprint, render_template, redirect, url_for, request, flash, g
from app import db
from app.forms import BuildingForm
from app.models import Building, Site
from app.routes.helpers import require_login, require_permission, org_query, org_get_or_404, parse_uuid, name_taken
buildings_bp = Blueprint('buildings', __name__, url_prefix="/buildings")
buildings_bp.before_request(require_login)
buildings_bp.before_request(require_permission)

# a building belongs to one site, set when it's created from the site's Buildings tab, and never moved,
# so the assets, jobs and job schedules in it always stay in its site. Names are unique within the site

def check_name(form, site, building=None):
    if name_taken(org_query(Building).filter(Building.site_id == site.id), Building.name, form.name.data, building):
        form.name.errors.append(f"{site.name} already has a building with this name.")
        return False
    return True

@buildings_bp.route('/')
def index():
    search = request.args.get('search', '').strip()
    page = request.args.get('page', 1, type=int)

    query = org_query(Building).join(Site, Building.site_id == Site.id)

    # set by "View imported items" on an import
    import_id = parse_uuid(request.args.get('import_id'))
    if import_id:
        query = query.filter(Building.import_event_id == import_id)

    if search:
        query = query.filter(db.or_(Building.name.ilike(f'%{search}%'), Site.name.ilike(f'%{search}%')))

    pagination = query.order_by(Site.name, Building.name, Building.id).paginate(page=page, per_page=25, error_out=False)

    return render_template('buildings/index.html', buildings=pagination.items, pagination=pagination, search=search, import_id=import_id)

# /buildings/create?site_id=<id>, from the site's Buildings tab
@buildings_bp.route('/create', methods=['GET', 'POST'])
def create():
    site = org_get_or_404(Site, parse_uuid(request.args.get('site_id')))
    form = BuildingForm()

    if form.validate_on_submit() and check_name(form, site):
        building = Building(
            name=form.name.data,
            site_id=site.id,
            organisation_id=g.person.organisation_id,
        )
        db.session.add(building)
        db.session.commit()
        flash(f"Building '{building.name}' created successfully!", 'success')
        return redirect(url_for('sites.buildings', id=site.id))

    return render_template('buildings/create.html', form=form, site=site)

@buildings_bp.route('/<uuid:id>', methods=['GET'])
def view(id):
    building = org_get_or_404(Building, id)
    return render_template('buildings/view/overview.html', building=building, active_tab="overview")

@buildings_bp.route('/<uuid:id>/edit', methods=['GET', 'POST'])
def edit(id):
    building = org_get_or_404(Building, id)

    form = BuildingForm(obj=building)

    if form.validate_on_submit() and check_name(form, building.site, building):
        building.name = form.name.data
        db.session.commit()
        flash(f"Building '{building.name}' updated successfully!", 'success')
        return redirect(url_for('buildings.view', id=building.id))

    return render_template('buildings/edit.html', building=building, form=form)

@buildings_bp.route('/<uuid:id>/delete', methods=['GET', 'POST'])
def delete(id):
    building = org_get_or_404(Building, id)

    if request.method == "POST":
        site_id = building.site_id
        db.session.delete(building)
        db.session.commit()
        flash(f"Building '{building.name}' deleted successfully!", 'success')
        return redirect(url_for('sites.buildings', id=site_id))

    return render_template('buildings/delete.html', building=building)

### RELATED DATA ###

@buildings_bp.route("/<uuid:id>/zones")
def zones(id):
    building = org_get_or_404(Building, id)
    return render_template("buildings/view/zones.html", building=building, active_tab="zones")

@buildings_bp.route("/<uuid:id>/assets")
def assets(id):
    building = org_get_or_404(Building, id)
    return render_template("buildings/view/assets.html", building=building, active_tab="assets")

@buildings_bp.route("/<uuid:id>/job-schedules")
def job_schedules(id):
    building = org_get_or_404(Building, id)
    return render_template("buildings/view/job_schedules.html", building=building, active_tab="job_schedules")
