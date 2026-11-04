from flask import Flask, render_template 
from config import Config
from app.extensions import db, migrate, csrf


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # Debug print
    # print("=== Full app.config ===")
    # for key, value in app.config.items():
    #     print(f"{key}: {value}")
    # print("=====================")

    

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)

    

    with app.app_context():
        from app import models  
        

    from app.routes.locations import locations_bp
    from app.routes.jobs import jobs_bp
    from app.routes.assets import assets_bp
    from app.routes.planned_jobs import planned_jobs_bp
    from app.routes.suppliers import suppliers_bp
    from app.routes.organisations import organisations_bp, register, terms
    from app.routes.auth import auth_bp, login
    from app.routes.dashboard import dashboard_bp, root
    from app.routes.admin import admin_bp
    from app.routes.people import people_bp
    from app.routes.user_groups import user_groups_bp
    from app.routes.imports import imports_bp
    from app.routes.exports import exports_bp
    from app.routes.support import support_bp
    from app.routes.my_organisation import my_organisation_bp
    from app.routes.documents import documents_bp
    from app.routes.reference_data import reference_data_bp
    from app.routes.notifications import notifications_bp

    app.register_blueprint(locations_bp)
    app.register_blueprint(jobs_bp)
    app.register_blueprint(assets_bp)
    app.register_blueprint(planned_jobs_bp)
    app.register_blueprint(suppliers_bp)
    app.register_blueprint(organisations_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(people_bp)
    app.register_blueprint(user_groups_bp)
    app.register_blueprint(imports_bp)
    app.register_blueprint(exports_bp)
    app.register_blueprint(support_bp)
    app.register_blueprint(my_organisation_bp)
    app.register_blueprint(documents_bp)
    app.register_blueprint(reference_data_bp)
    app.register_blueprint(notifications_bp)

    # / goes to the dashboard (which sends signed-out visitors to /login)
    app.add_url_rule('/', endpoint='dashboard.root', view_func=root)

    # registration, terms and login live at the root /register, /terms and /login urls rather than under their blueprints
    app.add_url_rule('/register', endpoint='organisations.register', view_func=register, methods=['GET', 'POST'])
    app.add_url_rule('/terms', endpoint='organisations.terms', view_func=terms)
    app.add_url_rule('/login', endpoint='auth.login', view_func=login, methods=['GET', 'POST'])

    from app.commands import make_platform_admin, create_jobs_for_planned_job_schedules
    app.cli.add_command(make_platform_admin)
    app.cli.add_command(create_jobs_for_planned_job_schedules)

    # times are stored in UTC and shown as UTC for now
    app.jinja_env.filters['datetime'] = lambda value: value.strftime('%d-%m-%Y %H:%M') if value else '-'
    app.jinja_env.filters['date'] = lambda value: value.strftime('%d-%m-%Y') if value else '-'
    # a time in UK time (like a job's completed_at, entered in UK time), shown back in UK time
    from app.services.auth import as_utc
    from app.services.scheduling import UK_TIMEZONE
    app.jinja_env.filters['uk_datetime'] = lambda value: as_utc(value).astimezone(UK_TIMEZONE).strftime('%d-%m-%Y %H:%M') if value else '-'
    app.jinja_env.filters['currency'] = lambda value: f'£{value:,.2f}' if value is not None else '-'
    # a number of days, e.g. 0.5 -> '0.5 days', 1 -> '1 day'
    from decimal import Decimal
    app.jinja_env.filters['days'] = lambda value: '-' if value is None else f"{Decimal(value).normalize():f} day{'' if value == 1 else 's'}"
    from app.services.documents import size_text
    app.jinja_env.filters['filesize'] = size_text

    # templates check permissions with can('jobs.edit') for the signed-in user in the session's organisation
    from flask import g
    from app.services.permissions import can, RESOURCES, ACTIONS, EXTRA_PERMISSIONS, NOT_GRANTABLE
    app.jinja_env.globals['can'] = lambda permission: can(g.get('person'), permission)
    app.jinja_env.globals['PERMISSION_RESOURCES'] = RESOURCES
    app.jinja_env.globals['PERMISSION_ACTIONS'] = ACTIONS
    app.jinja_env.globals['PERMISSION_EXTRAS'] = EXTRA_PERMISSIONS
    app.jinja_env.globals['PERMISSION_NOT_GRANTABLE'] = NOT_GRANTABLE

    # the navbar's bell: the signed-in person's unread notifications, as (count, the latest few)
    from app.services.notifications import NAVBAR_LIMIT, notification_url, unread_notifications
    def navbar_notifications():
        if not g.get('person'):
            return 0, []
        query = unread_notifications(g.person)
        return query.count(), query.limit(NAVBAR_LIMIT).all()
    app.jinja_env.globals['navbar_notifications'] = navbar_notifications
    app.jinja_env.globals['notification_url'] = notification_url

    @app.errorhandler(403)
    def forbidden(error):
        return render_template('errors/403.html'), 403

    # a request bigger than MAX_CONTENT_LENGTH, in practice a file upload: back to the form with a message
    @app.errorhandler(413)
    def too_large(error):
        from flask import flash, redirect, request
        from app.services.documents import MAX_FILE_MB
        flash(f'That file is too big. Files can be up to {MAX_FILE_MB} MB.', 'warning')
        return redirect(request.full_path.rstrip('?'))

    @app.errorhandler(404)
    def not_found(error):
        return render_template('errors/404.html'), 404

    # only used when debug is off: in debug mode Flask shows its debugger instead.
    # Flask has already logged the exception by the time this runs
    @app.errorhandler(500)
    def server_error(error):
        # the error may have left the transaction broken, so don't touch the database after this
        db.session.rollback()
        return render_template('errors/500.html', support_email=app.config['SUPPORT_EMAIL']), 500

    return app
