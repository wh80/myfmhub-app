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
        

    from app.routes.sites import sites_bp
    from app.routes.jobs import jobs_bp
    from app.routes.assets import assets_bp
    from app.routes.job_schedules import job_schedules_bp
    from app.routes.suppliers import suppliers_bp
    from app.routes.organisations import organisations_bp, register
    from app.routes.auth import auth_bp, login
    from app.routes.dashboard import dashboard_bp, root
    from app.routes.admin import admin_bp
    from app.routes.people import people_bp
    from app.routes.user_groups import user_groups_bp
    from app.routes.imports import imports_bp
    from app.routes.exports import exports_bp
    from app.routes.support import support_bp
    from app.routes.buildings import buildings_bp
    from app.routes.zones import zones_bp

    app.register_blueprint(sites_bp)
    app.register_blueprint(jobs_bp)
    app.register_blueprint(assets_bp)
    app.register_blueprint(job_schedules_bp)
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
    app.register_blueprint(buildings_bp)
    app.register_blueprint(zones_bp)

    # / goes to the dashboard (which sends signed-out visitors to /login)
    app.add_url_rule('/', endpoint='dashboard.root', view_func=root)

    # registration and login live at the root /register and /login urls rather than under their blueprints
    app.add_url_rule('/register', endpoint='organisations.register', view_func=register, methods=['GET', 'POST'])
    app.add_url_rule('/login', endpoint='auth.login', view_func=login, methods=['GET', 'POST'])

    from app.commands import make_platform_admin, create_jobs_for_job_schedules
    app.cli.add_command(make_platform_admin)
    app.cli.add_command(create_jobs_for_job_schedules)

    # times are stored in UTC and shown as UTC for now
    app.jinja_env.filters['datetime'] = lambda value: value.strftime('%d-%m-%Y %H:%M') if value else '-'
    app.jinja_env.filters['date'] = lambda value: value.strftime('%d-%m-%Y') if value else '-'
    app.jinja_env.filters['currency'] = lambda value: f'£{value:,.2f}' if value is not None else '-'

    # templates check permissions with can('jobs.edit') for the signed-in user
    from flask import g
    from app.services.permissions import can, RESOURCES, ACTIONS, EXTRA_PERMISSIONS
    app.jinja_env.globals['can'] = lambda permission: can(g.get('user'), permission)
    app.jinja_env.globals['PERMISSION_RESOURCES'] = RESOURCES
    app.jinja_env.globals['PERMISSION_ACTIONS'] = ACTIONS
    app.jinja_env.globals['PERMISSION_EXTRAS'] = EXTRA_PERMISSIONS

    @app.errorhandler(403)
    def forbidden(error):
        return render_template('errors/403.html'), 403

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
