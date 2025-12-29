from quart import Blueprint, request, jsonify
from app.database import SessionLocal
from app.utils.auth_utils import requires_auth
from app.models import User, UserPreference
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import and_
from datetime import datetime

preferences_bp = Blueprint("preferences", __name__, url_prefix="/api/preferences")

# Default preferences - this is what users get if they haven't customized anything
DEFAULT_PREFERENCES = {
    "pagination": {
        "clients": {"perPage": 10, "sort": "newest", "viewMode": "cards"},
        "leads": {"perPage": 10, "sort": "newest", "viewMode": "cards"},
        "projects": {"perPage": 10, "sort": "newest", "viewMode": "cards"},
        "admin_clients": {"perPage": 20, "sort": "newest", "viewMode": "table"},
        "admin_leads": {"perPage": 20, "sort": "newest", "viewMode": "table"},
        "admin_projects": {"perPage": 20, "sort": "newest", "viewMode": "table"},
        "admin_interactions": {"perPage": 20, "sort": "newest", "viewMode": "table"}
    },
    "display": {
        "sidebar_collapsed": False,
        "theme": "light"
    }
}

@preferences_bp.route("", methods=["GET"])
@preferences_bp.route("/", methods=["GET"])
@requires_auth()
async def get_user_preferences():
    """Get all user preferences with defaults merged in"""
    user = request.user
    session = SessionLocal()
    
    try:
        # Get all user preferences from database
        user_prefs = session.query(UserPreference).filter(
            UserPreference.user_id == user.id
        ).all()
        
        # Convert to nested structure
        preferences = {}
        for pref in user_prefs:
            if pref.category not in preferences:
                preferences[pref.category] = {}
            preferences[pref.category][pref.preference_key] = pref.preference_value
        
        # Merge with defaults (defaults take precedence for missing values)
        merged_preferences = merge_with_defaults(DEFAULT_PREFERENCES, preferences)
        
        return jsonify(merged_preferences)
        
    except SQLAlchemyError:
        session.rollback()
        return jsonify({"error": "Database error"}), 500
    finally:
        session.close()

@preferences_bp.route("/pagination/<table_name>", methods=["PUT"])
@requires_auth()
async def update_pagination_preference(table_name):
    """Update pagination preferences for a specific table"""
    user = request.user
    data = await request.get_json()
    session = SessionLocal()
    
    # Validate table name
    allowed_tables = [
        'clients', 'leads', 'projects', 'interactions',
        'admin_clients', 'admin_leads', 'admin_projects', 'admin_interactions'
    ]
    if table_name not in allowed_tables:
        return jsonify({"error": "Invalid table name"}), 400
    
    # Validate data
    per_page = data.get('perPage', 10)
    sort_order = data.get('sort', 'newest')
    view_mode = data.get('viewMode', 'cards')
    
    if not isinstance(per_page, int) or per_page < 1 or per_page > 100:
        return jsonify({"error": "Invalid perPage value (1-100)"}), 400
        
    if sort_order not in ['newest', 'oldest', 'alphabetical', 'pending', 'completed']:
        return jsonify({"error": "Invalid sort order"}), 400
    
    if view_mode not in ['cards', 'table']:
        view_mode = 'cards'
    
    try:
        # Find existing preference or create new
        existing_pref = session.query(UserPreference).filter(
            and_(
                UserPreference.user_id == user.id,
                UserPreference.category == 'pagination',
                UserPreference.preference_key == table_name
            )
        ).first()
        
        preference_value = {
            'perPage': per_page,
            'sort': sort_order,
            'viewMode': view_mode
        }
        
        if existing_pref:
            existing_pref.preference_value = preference_value
            existing_pref.updated_at = datetime.utcnow()
        else:
            new_pref = UserPreference(
                user_id=user.id,
                category='pagination',
                preference_key=table_name,
                preference_value=preference_value
            )
            session.add(new_pref)
        
        session.commit()
        
        return jsonify({
            "message": f"Pagination preferences updated for {table_name}",
            "preference": preference_value
        })
        
    except SQLAlchemyError:
        session.rollback()
        return jsonify({"error": "Database error"}), 500
    finally:
        session.close()

def merge_with_defaults(defaults, user_prefs):
    """Deep merge user preferences with defaults"""
    if not isinstance(user_prefs, dict):
        return defaults.copy()
        
    result = defaults.copy()
    
    for key, value in user_prefs.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge_with_defaults(result[key], value)
        else:
            result[key] = value
            
    return result