from datetime import datetime, timezone
from flask import Blueprint, render_template, request, g, Response, abort, flash, redirect, url_for
from app.models import Document, DocumentExport
from app.routes.helpers import require_login, require_permission, org_query, org_get_or_404
from app.services.auth import as_utc
from app.services.document_exports import EXPORT_KEEP_DAYS, active_document_export, start_document_export, tidy_document_exports
from app.services.documents import storage_text, storage_used_mb, stored_download
from app.services.exports import attachment
from app.services.permissions import can
from app.services.data_export import EXPORT_TYPES, export_zip
exports_bp = Blueprint('exports', __name__, url_prefix="/exports")
exports_bp.before_request(require_login)
exports_bp.before_request(require_permission)

# needs exports.manage (checked by require_permission). Each type also needs view on that type,
# so exporting can't show records the user's group can't already see

@exports_bp.route('/', methods=['GET', 'POST'])
def index():
    export_types = [export_type for export_type in EXPORT_TYPES.values() if can(g.person, export_type.permission)]
    error = None

    if request.method == 'POST':
        keys = set(request.form.getlist('types'))
        chosen = [export_type for export_type in export_types if export_type.key in keys]
        if chosen:
            files = [
                (export_type, org_query(export_type.model).options(*export_type.options()).order_by(export_type.model.created_at, export_type.model.id).all())
                for export_type in chosen
            ]
            return Response(export_zip(files), mimetype='application/zip', headers=attachment('myfmhub-export', 'zip'))
        error = 'Choose at least one type of data to export.'

    return render_template('exports/index.html', export_types=export_types, error=error, **documents_export_context())


# "Download all documents" also needs documents.view. Clicking it starts a thread that prepares the zip
# (services/document_exports.py), and the email links to download_documents, so the zip is only ever handed to
# someone signed in here
def require_documents_view():
    if not can(g.person, 'documents.view'):
        abort(403)


def documents_export_context():
    if not can(g.person, 'documents.view'):
        return {'show_documents_export': False}
    tidy_document_exports()
    return {
        'show_documents_export': True,
        'document_count': org_query(Document).count(),
        'storage_used': storage_text(storage_used_mb(g.person.organisation_id)),
        'active_export': active_document_export(g.person.organisation_id),
        'recent_exports': org_query(DocumentExport).order_by(DocumentExport.created_at.desc()).limit(5).all(),
        'keep_days': EXPORT_KEEP_DAYS,
    }


@exports_bp.route('/documents', methods=['POST'])
def request_documents():
    require_documents_view()
    error, _ = start_document_export(g.person)
    if error:
        flash(error, 'warning')
    else:
        flash(f"We're preparing a zip of all your documents. We'll email {g.user.email} a download link when it's ready.", 'success')
    return redirect(url_for('exports.index'))

@exports_bp.route('/documents/<uuid:id>')
def download_documents(id):
    require_documents_view()
    export = org_get_or_404(DocumentExport, id)
    if export.status != 'ready' or as_utc(export.expires_at) <= datetime.now(timezone.utc):
        flash("That documents export isn't available any more. You can ask for a new one below.", 'warning')
        return redirect(url_for('exports.index'))
    return stored_download(export.storage_key, 'application/zip', f'myfmhub-documents-{export.finished_at:%Y-%m-%d}.zip')
