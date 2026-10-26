from flask import Blueprint, render_template, redirect, url_for, request, flash, g
from app import db
from app.forms import SiteForm
from app.models import Site
from app.routes.helpers import require_login, require_permission, org_query, org_get_or_404, parse_uuid, name_taken
from app.services.exports import export_response
sites_bp = Blueprint('sites', __name__, url_prefix="/sites")
sites_bp.before_request(require_login)
sites_bp.before_request(require_permission)

# site names are unique within the organisation, ignoring case
def check_name(form, site=None):
    if name_taken(org_query(Site), Site.name, form.name.data, site):
        form.name.errors.append('There is already a site with this name.')
        return False
    return True

# the index table's columns, for downloads. Keys match data-col in sites/index.html
SITE_COLUMNS = [
    ('name', 'Name', lambda site: site.name),
    ('telephone', 'Telephone', lambda site: site.telephone),
]

# the index filters, shared by the page and its downloads
def filtered_sites():
    search = request.args.get('search', '').strip()

    query = org_query(Site)

    # set by "View imported items" on an import
    import_id = parse_uuid(request.args.get('import_id'))
    if import_id:
        query = query.filter(Site.import_event_id == import_id)

    if search:
        query = query.filter(Site.name.ilike(f'%{search}%'))

    return query.order_by(Site.name, Site.id), dict(search=search, import_id=import_id)

@sites_bp.route('/')
def index():
    query, filters = filtered_sites()
    page = request.args.get('page', 1, type=int)

    pagination = query.paginate(page=page, per_page=25, error_out=False)

    return render_template(
        'sites/index.html',
        sites=pagination.items,
        pagination=pagination,
        **filters
    )

@sites_bp.route('/export/<any(csv, pdf):file_format>')
def export(file_format):
    query, _ = filtered_sites()
    return export_response(file_format, 'sites', 'Sites', SITE_COLUMNS, query.all())

@sites_bp.route('/create', methods=['GET', 'POST'])
def create():
    form = SiteForm()
    
    if form.validate_on_submit() and check_name(form):
        site = Site(
            name=form.name.data,
            address_line_one=form.address_line_one.data,
            address_line_two=form.address_line_two.data,
            town_city=form.town_city.data,
            post_code=form.post_code.data,
            telephone=form.telephone.data,
            organisation_id=g.person.organisation_id,
        )
        db.session.add(site)
        db.session.commit()
        flash(f"Site '{site.name}' created successfully!", 'success')
        return redirect(url_for('sites.index'))

    return render_template('sites/create.html', form=form)

@sites_bp.route('/<uuid:id>', methods=['GET'])
def view(id):
    site = org_get_or_404(Site, id)

    return render_template('sites/view/overview.html', site=site, active_tab="overview",)

@sites_bp.route('/<uuid:id>/edit', methods=['GET', 'POST'])
def edit(id):
    site = org_get_or_404(Site, id)

    form=SiteForm(obj=site)

    if form.validate_on_submit() and check_name(form, site):
        
        site.name=form.name.data
        site.address_line_one=form.address_line_one.data
        site.address_line_two=form.address_line_two.data
        site.town_city=form.town_city.data
        site.post_code=form.post_code.data
        site.telephone=form.telephone.data
        
        db.session.commit()
        flash(f"Site '{site.name}' updated successfully!", 'success')
        return redirect(url_for('sites.view', id=site.id))

    return render_template('sites/edit.html', site=site, form=form)

@sites_bp.route('/<uuid:id>/delete', methods=['GET', 'POST'])
def delete(id):
    site = org_get_or_404(Site, id)

    if request.method =="POST":
        db.session.delete(site)
        db.session.commit()
        flash(f"Site '{site.name}' deleted successfully!", 'success')
        return redirect(url_for('sites.index'))

    return render_template('sites/delete.html', site=site)

### RELATED DATA ###

@sites_bp.route("/<uuid:id>/buildings")
def buildings(id):
    site = org_get_or_404(Site, id)
    return render_template("sites/view/buildings.html", site=site, active_tab="buildings",)

@sites_bp.route("/<uuid:id>/assets")
def assets(id):
    site = org_get_or_404(Site, id)
    return render_template("sites/view/assets.html", site=site, active_tab="assets",)


@sites_bp.route("/<uuid:id>/jobs")
def jobs(id):
    site = org_get_or_404(Site, id)
    return render_template("sites/view/jobs.html", site=site, active_tab="jobs",)

@sites_bp.route("/<uuid:id>/job-schedules")
def job_schedules(id):
    site = org_get_or_404(Site, id)
    return render_template("sites/view/job_schedules.html", site=site, active_tab="job_schedules",)