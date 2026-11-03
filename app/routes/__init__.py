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
from app.routes.subscriptions import subscriptions_bp
from app.routes.ai_connections import ai_connections_bp
from app.routes.oauth import oauth_bp
from app.routes.project_archive import project_archive_bp
from app.routes.ai_actions import ai_actions_bp

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
    # Whole-database operations are not available to tenant accounts.
    app.register_blueprint(subscriptions_bp)
    app.register_blueprint(ai_connections_bp)
    app.register_blueprint(oauth_bp)
    app.register_blueprint(project_archive_bp)
    app.register_blueprint(ai_actions_bp)
    from app.mcp_server import install_mcp
    install_mcp(app)
