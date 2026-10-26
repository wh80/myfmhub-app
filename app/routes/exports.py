from flask import Blueprint, render_template, request, g, Response
from app.routes.helpers import require_login, require_permission, org_query
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

    return render_template('exports/index.html', export_types=export_types, error=error)
