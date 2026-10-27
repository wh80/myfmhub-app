from flask import Blueprint, render_template, redirect, url_for, request, flash, g
from app import db
from app.forms import ZoneForm
from app.models import Zone, Building, Site
from app.routes.helpers import require_login, require_permission, org_query, org_get_or_404, parse_uuid, name_taken, files_tab
zones_bp = Blueprint('zones', __name__, url_prefix="/zones")
zones_bp.before_request(require_login)
zones_bp.before_request(require_permission)

# a zone belongs to one building, set when it's created from the building's Zones tab, and never moved.
# Names are unique within the building

def check_name(form, building, zone=None):
    if name_taken(org_query(Zone).filter(Zone.building_id == building.id), Zone.name, form.name.data, zone):
        form.name.errors.append(f"{building.name} already has a zone with this name.")
        return False
    return True

@zones_bp.route('/')
def index():
    search = request.args.get('search', '').strip()
    page = request.args.get('page', 1, type=int)

    query = (
        org_query(Zone)
        .join(Building, Zone.building_id == Building.id)
        .join(Site, Building.site_id == Site.id)
    )

    # set by "View imported items" on an import
    import_id = parse_uuid(request.args.get('import_id'))
    if import_id:
        query = query.filter(Zone.import_event_id == import_id)

    if search:
        query = query.filter(db.or_(
            Zone.name.ilike(f'%{search}%'),
            Building.name.ilike(f'%{search}%'),
            Site.name.ilike(f'%{search}%'),
        ))

    pagination = query.order_by(Site.name, Building.name, Zone.name, Zone.id).paginate(page=page, per_page=25, error_out=False)

    return render_template('zones/index.html', zones=pagination.items, pagination=pagination, search=search, import_id=import_id)

# /zones/create?building_id=<id>, from the building's Zones tab
@zones_bp.route('/create', methods=['GET', 'POST'])
def create():
    building = org_get_or_404(Building, parse_uuid(request.args.get('building_id')))
    form = ZoneForm()

    if form.validate_on_submit() and check_name(form, building):
        zone = Zone(
            name=form.name.data,
            building_id=building.id,
            organisation_id=g.person.organisation_id,
        )
        db.session.add(zone)
        db.session.commit()
        flash(f"Zone '{zone.name}' created successfully!", 'success')
        return redirect(url_for('buildings.zones', id=building.id))

    return render_template('zones/create.html', form=form, building=building)

@zones_bp.route('/<uuid:id>', methods=['GET'])
def view(id):
    zone = org_get_or_404(Zone, id)
    return render_template('zones/view/overview.html', zone=zone, active_tab="overview")

@zones_bp.route('/<uuid:id>/edit', methods=['GET', 'POST'])
def edit(id):
    zone = org_get_or_404(Zone, id)

    form = ZoneForm(obj=zone)

    if form.validate_on_submit() and check_name(form, zone.building, zone):
        zone.name = form.name.data
        db.session.commit()
        flash(f"Zone '{zone.name}' updated successfully!", 'success')
        return redirect(url_for('zones.view', id=zone.id))

    return render_template('zones/edit.html', zone=zone, form=form)

@zones_bp.route('/<uuid:id>/delete', methods=['GET', 'POST'])
def delete(id):
    zone = org_get_or_404(Zone, id)

    if request.method == "POST":
        building_id = zone.building_id
        db.session.delete(zone)
        db.session.commit()
        flash(f"Zone '{zone.name}' deleted successfully!", 'success')
        return redirect(url_for('buildings.zones', id=building_id))

    return render_template('zones/delete.html', zone=zone)

### RELATED DATA ###

@zones_bp.route("/<uuid:id>/files")
def files(id):
    return files_tab(Zone, id, "zones/view/files.html", "zone")
