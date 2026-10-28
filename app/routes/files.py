from flask import Blueprint, render_template, redirect, url_for, request, flash, g, abort, current_app
from app import db
from app.forms import FileUploadForm, FileEditForm
from app.models import File, FileCategory
from app.routes.helpers import selected_ids, require_login, require_permission, org_query, org_get_or_404, safe_next_url, parse_uuid, related, related_page, set_reference_choices, reference_items, filter_reference
from app.services.files import FILE_PARENTS, MAX_FILE_MB, FILE_TYPES, create_file, delete_files, file_links, view_response
from app.services.permissions import can
files_bp = Blueprint('files', __name__, url_prefix="/files")
files_bp.before_request(require_login)
files_bp.before_request(require_permission)

# files belong to the organisation and link to any number of sites, buildings, zones, assets, jobs,
# job schedules, people and suppliers (FILE_PARENTS). Each of those has a Files tab ('<resource>.files')
# whose Add New, Link Existing and Remove File buttons come here with the resource and the record's id.
# Linking and unlinking need edit on that record, like linking assets to a job


def parent_record(resource, id, permission='edit'):
    """The FILE_PARENTS entry and the record a file is being added to or removed from. 404 for an
    unknown resource or another organisation's record, 403 without edit on it."""
    parent = FILE_PARENTS.get(resource)
    if parent is None:
        abort(404)
    record = org_get_or_404(parent.model, id)
    if not can(g.person, f'{resource}.{permission}'):
        abort(403)
    return parent, record


def filtered_files():
    search = request.args.get('search', '').strip()
    query = org_query(File)
    if search:
        query = query.filter(db.or_(File.description.ilike(f'%{search}%'), File.filename.ilike(f'%{search}%')))
    return query, search


@files_bp.route('/')
def index():
    query, search = filtered_files()
    query, file_category_id = filter_reference(query, File, 'file_category_id')
    page = request.args.get('page', 1, type=int)
    pagination = query.order_by(File.description, File.id).paginate(page=page, per_page=25, error_out=False)
    return render_template('files/index.html', files=pagination.items, pagination=pagination, search=search,
                           file_categories=reference_items(FileCategory), file_category_id=file_category_id)

# Add New: from the Files index (?resource= not given) or a record's Files tab, which it's linked to
@files_bp.route('/new', methods=['GET', 'POST'])
def create():
    parent = record = None
    if request.args.get('resource'):
        parent, record = parent_record(request.args['resource'], parse_uuid(request.args.get('id')))
    back_url = url_for(f'{parent.key}.files', id=record.id) if record else url_for('files.index')

    form = FileUploadForm()
    set_reference_choices(form)
    if form.validate_on_submit():
        try:
            file = create_file(form.file.data, form.description.data, form.expires.data, g.person.organisation_id, link_to=record,
                               file_category_id=parse_uuid(form.file_category_id.data))
        except Exception:
            current_app.logger.exception('Failed to upload a file')
            flash("The file couldn't be uploaded. Please try again.", 'warning')
        else:
            flash(f"File '{file.description}' uploaded successfully!", 'success')
            return redirect(back_url)

    return render_template('files/create.html', form=form, parent=parent, record=record, back_url=back_url,
                           max_file_mb=MAX_FILE_MB, file_types=sorted(FILE_TYPES))

@files_bp.route('/<uuid:id>/view')
def view(id):
    return view_response(org_get_or_404(File, id))

# only the description, expiry date and category. The stored file, its name, type and size never change.
# ?next= is the list the Edit button was on (the Files index or a record's Files tab)
@files_bp.route('/<uuid:id>/edit', methods=['GET', 'POST'])
def edit(id):
    file = org_get_or_404(File, id)
    next_url = safe_next_url(request.args.get('next')) or url_for('files.index')

    form = FileEditForm(obj=file)
    set_reference_choices(form)

    if form.validate_on_submit():
        file.description = form.description.data
        file.expires = form.expires.data
        file.file_category_id = parse_uuid(form.file_category_id.data)
        db.session.commit()
        flash(f"File '{file.description}' updated successfully!", 'success')
        return redirect(next_url)

    return render_template('files/edit.html', file=file, form=form, next_url=next_url)

@files_bp.route('/link/<resource>/<uuid:id>', methods=['GET', 'POST'])
def link(resource, id):
    parent, record = parent_record(resource, id)
    # files in the organisation that aren't linked to the record yet
    query = org_query(File).filter(~getattr(File, resource).any(parent.model.id == record.id))

    if request.method == 'POST':
        ids = selected_ids('file_ids')
        chosen = query.filter(File.id.in_(ids)).all() if ids else []
        if not chosen:
            flash("Select at least one file to link.", 'warning')
            return redirect(request.full_path)
        record.files.extend(chosen)
        db.session.commit()
        flash(f"Linked {len(chosen)} file(s) to '{parent.name(record)}'.", 'success')
        return redirect(url_for(f'{resource}.files', id=record.id))

    pagination, search = related_page(query, File)
    return render_template('files/link.html', parent=parent, record=record, files=pagination.items, pagination=pagination, search=search)

# Remove File: the form's Unlink File button posts here. Its Unlink & Delete File button posts the same
# ticked files to files.delete instead, which deletes them (and so all their links) after a confirmation
@files_bp.route('/unlink/<resource>/<uuid:id>', methods=['GET', 'POST'])
def unlink(resource, id):
    parent, record = parent_record(resource, id)

    if request.method == 'POST':
        ids = selected_ids('file_ids')
        chosen = [file for file in record.files if file.id in ids]
        if not chosen:
            flash("Select at least one file.", 'warning')
            return redirect(request.full_path)
        for file in chosen:
            record.files.remove(file)
        db.session.commit()
        flash(f"Unlinked {len(chosen)} file(s) from '{parent.name(record)}'.", 'success')
        return redirect(url_for(f'{resource}.files', id=record.id))

    pagination, search = related_page(related(record, parent.model.files, File), File)
    return render_template('files/unlink.html', parent=parent, record=record, files=pagination.items, pagination=pagination, search=search)

# GET: tick files to delete (the Files index's Delete Files button). POST with ticked file_ids shows the
# confirmation, listing what each file is linked to; POST with confirmed=1 deletes them
@files_bp.route('/delete', methods=['GET', 'POST'])
def delete():
    next_url = safe_next_url(request.values.get('next')) or url_for('files.index')

    if request.method == 'POST':
        ids = selected_ids('file_ids')
        files = org_query(File).filter(File.id.in_(ids)).order_by(File.description, File.id).all() if ids else []
        if not files:
            flash("Select at least one file to delete.", 'warning')
            # back to the page the files were ticked on
            return redirect(safe_next_url(request.form.get('back')) or next_url)

        if request.form.get('confirmed') == '1':
            delete_files(files)
            flash(f"Deleted {len(files)} file(s).", 'success')
            return redirect(next_url)

        return render_template('files/delete_confirm.html', files=[(file, file_links(file)) for file in files], next_url=next_url)

    query, search = filtered_files()
    files = query.order_by(File.description, File.id).all()
    return render_template('files/delete.html', files=files, search=search)
