from flask import Blueprint, render_template, redirect, url_for, request, abort, flash, g
from app import db
from app.forms import ReferenceItemForm, ReferenceBulkForm
from app.routes.helpers import require_login, require_permission, org_query, org_get_or_404
from app.services.reference_data import REFERENCE_TYPES, MAX_BULK_ITEMS, add_items, delete_item, find_item, item_usage, split_values
reference_data_bp = Blueprint('reference_data', __name__, url_prefix="/reference-data")
reference_data_bp.before_request(require_login)
reference_data_bp.before_request(require_permission)

# the organisation's categories and conditions. Every route needs reference_data.manage (checked by
# require_permission). Choosing them on records only needs edit on the record

def reference_type_or_404(slug):
    reference_type = REFERENCE_TYPES.get(slug)
    if reference_type is None:
        abort(404)
    return reference_type

# a description already used by another item of the type, ignoring case. Adds the error to the form
def check_description(form, reference_type, item=None):
    existing = find_item(reference_type.model, g.person.organisation_id, form.description.data)
    if existing is not None and existing is not item:
        form.description.errors.append(f"There's already a {reference_type.singular} called '{existing.description}'.")
        return False
    return True

@reference_data_bp.route('/')
def index():
    counts = {slug: org_query(reference_type.model).count() for slug, reference_type in REFERENCE_TYPES.items()}
    return render_template('reference_data/index.html', reference_types=REFERENCE_TYPES.values(), counts=counts)

# the list, with a form to add one more
@reference_data_bp.route('/<slug>', methods=['GET', 'POST'])
def items(slug):
    reference_type = reference_type_or_404(slug)
    model = reference_type.model
    form = ReferenceItemForm()

    if form.validate_on_submit() and check_description(form, reference_type):
        item = model(description=form.description.data, organisation_id=g.person.organisation_id)
        db.session.add(item)
        db.session.commit()
        flash(f"Added '{item.description}'.", 'success')
        return redirect(url_for('reference_data.items', slug=slug))

    items = org_query(model).order_by(model.description, model.id).all()
    return render_template('reference_data/items.html', reference_type=reference_type, items=items, form=form)

@reference_data_bp.route('/<slug>/<uuid:id>/edit', methods=['GET', 'POST'])
def edit(slug, id):
    reference_type = reference_type_or_404(slug)
    item = org_get_or_404(reference_type.model, id)
    form = ReferenceItemForm(obj=item)

    if form.validate_on_submit() and check_description(form, reference_type, item):
        item.description = form.description.data
        db.session.commit()
        flash(f"Updated '{item.description}'.", 'success')
        return redirect(url_for('reference_data.items', slug=slug))

    return render_template('reference_data/edit.html', reference_type=reference_type, item=item, form=form)

# deleting clears the item from the records that use it (the page says how many)
@reference_data_bp.route('/<slug>/<uuid:id>/delete', methods=['GET', 'POST'])
def delete(slug, id):
    reference_type = reference_type_or_404(slug)
    item = org_get_or_404(reference_type.model, id)

    if request.method == 'POST':
        delete_item(reference_type, item)
        db.session.commit()
        flash(f"Deleted '{item.description}'.", 'success')
        return redirect(url_for('reference_data.items', slug=slug))

    return render_template('reference_data/delete.html', reference_type=reference_type, item=item, usage=item_usage(reference_type, item))

# Add in bulk: type or paste values separated by commas or new lines
@reference_data_bp.route('/<slug>/add-in-bulk', methods=['GET', 'POST'])
def bulk(slug):
    reference_type = reference_type_or_404(slug)
    form = ReferenceBulkForm()

    if form.validate_on_submit():
        if len(split_values(form.values.data)) > MAX_BULK_ITEMS:
            form.values.errors.append(f"Add up to {MAX_BULK_ITEMS} at a time.")
        else:
            added, skipped, too_long = add_items(reference_type, g.person.organisation_id, form.values.data)
            db.session.commit()
            flash(f"Added {len(added)} {reference_type.label.lower() if len(added) != 1 else reference_type.singular}.", 'success' if added else 'warning')
            if skipped:
                shown = ', '.join(skipped[:10]) + (f' and {len(skipped) - 10} more' if len(skipped) > 10 else '')
                flash(f"Skipped {len(skipped)} already in the list: {shown}.", 'warning')
            if too_long:
                flash(f"Skipped {len(too_long)} longer than 100 characters.", 'warning')
            return redirect(url_for('reference_data.items', slug=slug))

    return render_template('reference_data/bulk.html', reference_type=reference_type, form=form, max_items=MAX_BULK_ITEMS)
