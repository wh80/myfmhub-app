from flask import Blueprint, render_template, redirect, url_for, request, flash, g
from app import db
from app.forms import SupplierForm
from app.models import Supplier, SupplierCategory
from app.routes.helpers import require_login, require_permission, org_query, org_get_or_404, parse_uuid, documents_tab, set_reference_choices, reference_items, filter_reference
from app.services.addresses import clean_address
from app.services.exports import export_response
suppliers_bp = Blueprint('suppliers', __name__, url_prefix="/suppliers")
suppliers_bp.before_request(require_login)
suppliers_bp.before_request(require_permission)

# the index table's columns, for downloads. Keys match data-col in suppliers/index.html
SUPPLIER_COLUMNS = [
    ('name', 'Name', lambda supplier: supplier.name),
    ('telephone', 'Telephone', lambda supplier: supplier.telephone),
    ('category', 'Supplier Category', lambda supplier: supplier.supplier_category.description if supplier.supplier_category else None),
]

# the index filters, shared by the page and its downloads
def filtered_suppliers():
    search = request.args.get('search', '').strip()

    query = org_query(Supplier)

    # set by "View imported items" on an import
    import_id = parse_uuid(request.args.get('import_id'))
    if import_id:
        query = query.filter(Supplier.import_event_id == import_id)

    if search:
        query = query.filter(Supplier.name.ilike(f'%{search}%'))

    query, supplier_category_id = filter_reference(query, Supplier, 'supplier_category_id')

    return query.order_by(Supplier.name, Supplier.id), dict(search=search, import_id=import_id, supplier_category_id=supplier_category_id)

@suppliers_bp.route('/')
def index():
    query, filters = filtered_suppliers()
    page = request.args.get('page', 1, type=int)

    pagination = query.paginate(page=page, per_page=25, error_out=False)

    return render_template(
        'suppliers/index.html',
        suppliers=pagination.items,
        pagination=pagination,
        supplier_categories=reference_items(SupplierCategory),
        **filters
    )

@suppliers_bp.route('/export/<any(xlsx, pdf):file_format>')
def export(file_format):
    query, _ = filtered_suppliers()
    return export_response(file_format, 'suppliers', 'Suppliers', SUPPLIER_COLUMNS, query.all())

@suppliers_bp.route('/create', methods=['GET', 'POST'])
def create():
    form = SupplierForm()
    set_reference_choices(form)
    
    if form.validate_on_submit():
        supplier = Supplier(
            name=form.name.data,
            address=clean_address(form.address.data),
            telephone=form.telephone.data,
            email=form.email.data or None,
            supplier_category_id=parse_uuid(form.supplier_category_id.data),
            organisation_id=g.person.organisation_id,
        )
        db.session.add(supplier)
        db.session.commit()
        flash(f"Supplier '{supplier.name}' created successfully!", 'success')
        return redirect(url_for('suppliers.index'))

    return render_template('suppliers/create.html', form=form)

@suppliers_bp.route('/<uuid:id>', methods=['GET'])
def view(id):
    supplier = org_get_or_404(Supplier, id)

    return render_template('suppliers/view/overview.html', supplier=supplier, active_tab="overview")

@suppliers_bp.route('/<uuid:id>/edit', methods=['GET', 'POST'])
def edit(id):
    supplier = org_get_or_404(Supplier, id)

    form=SupplierForm(obj=supplier)
    set_reference_choices(form)

    if form.validate_on_submit():
        
        supplier.name=form.name.data
        supplier.address=clean_address(form.address.data)
        supplier.telephone=form.telephone.data
        supplier.email=form.email.data or None
        supplier.supplier_category_id=parse_uuid(form.supplier_category_id.data)
        
        db.session.commit()
        flash(f"Supplier '{supplier.name}' updated successfully!", 'success')
        return redirect(url_for('suppliers.view', id=supplier.id))

    return render_template('suppliers/edit.html', supplier=supplier, form=form)

@suppliers_bp.route('/<uuid:id>/delete', methods=['GET', 'POST'])
def delete(id):
    supplier = org_get_or_404(Supplier, id)

    # a job's assignee is kept for audit (Job.assigned_supplier_id is RESTRICT)
    if supplier.assigned_jobs:
        flash(f"Supplier '{supplier.name}' can't be deleted because it has jobs assigned to it, which are kept for audit purposes. Archiving will be available instead in a future update.", 'warning')
        return redirect(url_for('suppliers.view', id=supplier.id))

    if request.method =="POST":
        db.session.delete(supplier)
        db.session.commit()
        flash(f"Supplier '{supplier.name}' deleted successfully!", 'success')
        return redirect(url_for('suppliers.index'))

    return render_template('suppliers/delete.html', supplier=supplier)

### RELATED DATA ###

@suppliers_bp.route("/<uuid:id>/documents")
def documents(id):
    return documents_tab(Supplier, id, "suppliers/view/documents.html", "supplier")
