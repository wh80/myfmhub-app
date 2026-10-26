from flask import Blueprint, render_template, redirect, url_for, request, abort, flash, g, Response
from app.forms import ImportForm
from app.models import ImportEvent
from app.routes.helpers import require_login, require_permission, org_query, org_get_or_404
from app.services.permissions import can
from app.services.imports import IMPORT_TYPES, MAX_BYTES, MAX_ROWS, read_csv, remaining_records, run_import, template_csv, undo_blockers, undo_import
imports_bp = Blueprint('imports', __name__, url_prefix="/imports")
imports_bp.before_request(require_login)
imports_bp.before_request(require_permission)

# every route needs imports.manage (checked by require_permission). It covers importing every type,
# whatever else the user's group allows. Undo also needs delete on the type, see undo()
def import_type_or_404(slug):
    import_type = IMPORT_TYPES.get(slug.replace('-', '_'))
    if import_type is None:
        abort(404)
    return import_type

@imports_bp.route('/')
def index():
    page = request.args.get('page', 1, type=int)

    pagination = org_query(ImportEvent).order_by(ImportEvent.created_at.desc(), ImportEvent.id).paginate(
        page=page, per_page=25, error_out=False
    )
    return render_template(
        'imports/index.html',
        import_events=pagination.items,
        pagination=pagination,
        import_types=IMPORT_TYPES.values(),
        IMPORT_TYPES=IMPORT_TYPES,
    )

@imports_bp.route('/new/<slug>', methods=['GET', 'POST'])
def upload(slug):
    import_type = import_type_or_404(slug)
    form = ImportForm()
    problems = []

    if form.validate_on_submit():
        upload = form.file.data
        # read one byte past the limit, so read_csv can tell the file is too big without reading it all
        rows, problems = read_csv(import_type, upload.read(MAX_BYTES + 1))
        if not problems:
            import_event = run_import(import_type, upload.filename or 'upload.csv', rows, g.person)
            return redirect(url_for('imports.view', id=import_event.id))

    return render_template(
        'imports/upload.html',
        import_type=import_type,
        form=form,
        problems=problems,
        max_rows=MAX_ROWS,
        max_mb=MAX_BYTES // (1024 * 1024),
    )

@imports_bp.route('/new/<slug>/template.csv')
def template(slug):
    import_type = import_type_or_404(slug)
    return Response(
        template_csv(import_type),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{import_type.slug}-import-template.csv"'},
    )

@imports_bp.route('/<uuid:id>')
def view(id):
    import_event = org_get_or_404(ImportEvent, id)
    return render_template(
        'imports/view.html',
        import_event=import_event,
        import_type=IMPORT_TYPES.get(import_event.import_type),
    )

@imports_bp.route('/<uuid:id>/undo', methods=['GET', 'POST'])
def undo(id):
    import_event = org_get_or_404(ImportEvent, id)
    import_type = IMPORT_TYPES.get(import_event.import_type)
    if import_type is None:
        abort(404)
    if not can(g.person, import_type.delete_permission):
        abort(403)

    if import_event.undone_at:
        flash('This import has already been undone.', 'warning')
        return redirect(url_for('imports.view', id=import_event.id))

    blockers = undo_blockers(import_type, import_event, g.person)

    if request.method == 'POST':
        if blockers:
            flash("This import can't be undone yet.", 'warning')
            return redirect(url_for('imports.undo', id=import_event.id))
        count = undo_import(import_type, import_event, g.person)
        flash(f"Import undone. {count} {'record' if count == 1 else 'records'} deleted.", 'success')
        return redirect(url_for('imports.view', id=import_event.id))

    return render_template(
        'imports/undo.html',
        import_event=import_event,
        import_type=import_type,
        remaining=len(remaining_records(import_type, import_event)),
        blockers=blockers,
    )
