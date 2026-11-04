from flask import Blueprint, render_template, redirect, url_for, request, flash, g, abort, current_app
from app import db
from app.forms import DocumentUploadForm, DocumentEditForm
from app.models import Document, DocumentCategory
from app.routes.helpers import selected_ids, parse_date, require_login, require_permission, org_query, org_get_or_404, safe_next_url, parse_uuid, related, related_page, set_reference_choices, reference_items, filter_reference
from app.services.documents import DOCUMENT_PARENTS, MAX_FILE_MB, FILE_TYPES, create_document, delete_documents, document_links, view_response
from app.services.permissions import can
documents_bp = Blueprint('documents', __name__, url_prefix="/documents")
documents_bp.before_request(require_login)
documents_bp.before_request(require_permission)

# documents belong to the organisation and link to any number of locations, assets, jobs,
# planned job schedules, people and suppliers (DOCUMENT_PARENTS). Each of those has a Documents tab ('<resource>.documents')
# whose Add New, Link Existing and Remove Document buttons come here with the resource and the record's id.
# Linking and unlinking need edit on that record, like linking assets to a job


def parent_record(resource, id, permission='edit'):
    """The DOCUMENT_PARENTS entry and the record a document is being added to or removed from. 404 for an
    unknown resource or another organisation's record, 403 without edit on it."""
    parent = DOCUMENT_PARENTS.get(resource)
    if parent is None:
        abort(404)
    record = org_get_or_404(parent.model, id)
    if not can(g.person, f'{resource}.{permission}'):
        abort(403)
    return parent, record


def filtered_documents():
    search = request.args.get('search', '').strip()
    query = org_query(Document)
    if search:
        query = query.filter(db.or_(Document.description.ilike(f'%{search}%'), Document.filename.ilike(f'%{search}%')))
    return query, search


@documents_bp.route('/')
def index():
    query, search = filtered_documents()
    query, document_category_id = filter_reference(query, Document, 'document_category_id')
    expires_from = parse_date(request.args.get('expires_from'))
    expires_to = parse_date(request.args.get('expires_to'))
    if expires_from:
        query = query.filter(Document.expires >= expires_from)
    if expires_to:
        query = query.filter(Document.expires <= expires_to)
    page = request.args.get('page', 1, type=int)
    pagination = query.order_by(Document.description, Document.id).paginate(page=page, per_page=25, error_out=False)
    return render_template('documents/index.html', documents=pagination.items, pagination=pagination, search=search,
                           document_categories=reference_items(DocumentCategory), document_category_id=document_category_id,
                           expires_from=expires_from, expires_to=expires_to)

# Add New: from the Documents index (?resource= not given) or a record's Documents tab, which it's linked to
@documents_bp.route('/new', methods=['GET', 'POST'])
def create():
    parent = record = None
    if request.args.get('resource'):
        parent, record = parent_record(request.args['resource'], parse_uuid(request.args.get('id')))
    back_url = url_for(f'{parent.key}.documents', id=record.id) if record else url_for('documents.index')

    form = DocumentUploadForm()
    set_reference_choices(form)
    if form.validate_on_submit():
        try:
            document = create_document(form.file.data, form.description.data, form.expires.data, g.person.organisation_id, link_to=record,
                               document_category_id=parse_uuid(form.document_category_id.data))
        except Exception:
            current_app.logger.exception('Failed to upload a document')
            flash("The document couldn't be uploaded. Please try again.", 'warning')
        else:
            flash(f"Document '{document.description}' uploaded successfully!", 'success')
            return redirect(back_url)

    return render_template('documents/create.html', form=form, parent=parent, record=record, back_url=back_url,
                           max_file_mb=MAX_FILE_MB, file_types=sorted(FILE_TYPES))

@documents_bp.route('/<uuid:id>/view')
def view(id):
    return view_response(org_get_or_404(Document, id))

# only the description, expiry date and category. The stored document, its name, type and size never change.
# ?next= is the list the Edit button was on (the Documents index or a record's Documents tab)
@documents_bp.route('/<uuid:id>/edit', methods=['GET', 'POST'])
def edit(id):
    document = org_get_or_404(Document, id)
    next_url = safe_next_url(request.args.get('next')) or url_for('documents.index')

    form = DocumentEditForm(obj=document)
    set_reference_choices(form)

    if form.validate_on_submit():
        document.description = form.description.data
        document.expires = form.expires.data
        document.document_category_id = parse_uuid(form.document_category_id.data)
        db.session.commit()
        flash(f"Document '{document.description}' updated successfully!", 'success')
        return redirect(next_url)

    return render_template('documents/edit.html', document=document, form=form, next_url=next_url)

@documents_bp.route('/link/<resource>/<uuid:id>', methods=['GET', 'POST'])
def link(resource, id):
    parent, record = parent_record(resource, id)
    # documents in the organisation that aren't linked to the record yet
    query = org_query(Document).filter(~getattr(Document, resource).any(parent.model.id == record.id))

    if request.method == 'POST':
        ids = selected_ids('document_ids')
        chosen = query.filter(Document.id.in_(ids)).all() if ids else []
        if not chosen:
            flash("Select at least one document to link.", 'warning')
            return redirect(request.full_path)
        record.documents.extend(chosen)
        db.session.commit()
        flash(f"Linked {len(chosen)} document(s) to '{parent.name(record)}'.", 'success')
        return redirect(url_for(f'{resource}.documents', id=record.id))

    pagination, search = related_page(query, Document)
    return render_template('documents/link.html', parent=parent, record=record, documents=pagination.items, pagination=pagination, search=search)

# Remove Document: the form's Unlink Document button posts here. Its Unlink & Delete Document button posts the same
# ticked documents to documents.delete instead, which deletes them (and so all their links) after a confirmation
@documents_bp.route('/unlink/<resource>/<uuid:id>', methods=['GET', 'POST'])
def unlink(resource, id):
    parent, record = parent_record(resource, id)

    if request.method == 'POST':
        ids = selected_ids('document_ids')
        chosen = [document for document in record.documents if document.id in ids]
        if not chosen:
            flash("Select at least one document.", 'warning')
            return redirect(request.full_path)
        for document in chosen:
            record.documents.remove(document)
        db.session.commit()
        flash(f"Unlinked {len(chosen)} document(s) from '{parent.name(record)}'.", 'success')
        return redirect(url_for(f'{resource}.documents', id=record.id))

    pagination, search = related_page(related(record, parent.model.documents, Document), Document)
    return render_template('documents/unlink.html', parent=parent, record=record, documents=pagination.items, pagination=pagination, search=search)

# GET: tick documents to delete (the Documents index's Delete Documents button). POST with ticked document_ids shows the
# confirmation, listing what each document is linked to; POST with confirmed=1 deletes them
@documents_bp.route('/delete', methods=['GET', 'POST'])
def delete():
    next_url = safe_next_url(request.values.get('next')) or url_for('documents.index')

    if request.method == 'POST':
        ids = selected_ids('document_ids')
        documents = org_query(Document).filter(Document.id.in_(ids)).order_by(Document.description, Document.id).all() if ids else []
        if not documents:
            flash("Select at least one document to delete.", 'warning')
            # back to the page the documents were ticked on
            return redirect(safe_next_url(request.form.get('back')) or next_url)

        if request.form.get('confirmed') == '1':
            delete_documents(documents)
            flash(f"Deleted {len(documents)} document(s).", 'success')
            return redirect(next_url)

        return render_template('documents/delete_confirm.html', documents=[(document, document_links(document)) for document in documents], next_url=next_url)

    query, search = filtered_documents()
    documents = query.order_by(Document.description, Document.id).all()
    return render_template('documents/delete.html', documents=documents, search=search)
