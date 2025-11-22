from quart import Blueprint
from app.routes.accounts import accounts_bp
from app.routes.auth import auth_bp
from app.routes.clients import clients_bp
from app.routes.leads import leads_bp
from app.routes.reports import reports_bp
from app.routes.projects import projects_bp
from app.routes.interactions import interactions_bp
from app.routes.activity import activity_bp
from app.routes.users import users_bp
from app.routes.search import search_bp
from app.routes.utils import utils_bp
from app.routes.contacts import contacts_bp
from app.routes.imports import imports_bp
from app.routes.user_preferences import preferences_bp
from app.routes.storage import storage_bp

def register_blueprints(app):
    app.register_blueprint(auth_bp)
    app.register_blueprint(accounts_bp)
    app.register_blueprint(clients_bp)
    app.register_blueprint(leads_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(projects_bp)
    app.register_blueprint(interactions_bp)
    app.register_blueprint(activity_bp)
    app.register_blueprint(users_bp)
    app.register_blueprint(search_bp)
    app.register_blueprint(utils_bp)
    app.register_blueprint(contacts_bp)
    app.register_blueprint(imports_bp)
    app.register_blueprint(preferences_bp)
    app.register_blueprint(storage_bp)