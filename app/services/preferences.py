"""Current-user preferences with explicit ownership even outside HTTP/RLS."""
from copy import deepcopy
from datetime import datetime

from app.models import User, UserPreference
from app.services.base import TenantService


DEFAULT_PREFERENCES = {
    'pagination': {
        'clients': {'perPage': 10, 'sort': 'newest', 'viewMode': 'cards'},
        'leads': {'perPage': 10, 'sort': 'newest', 'viewMode': 'cards'},
        'projects': {'perPage': 10, 'sort': 'newest', 'viewMode': 'cards'},
        'admin_clients': {'perPage': 20, 'sort': 'newest', 'viewMode': 'table'},
        'admin_leads': {'perPage': 20, 'sort': 'newest', 'viewMode': 'table'},
        'admin_projects': {'perPage': 20, 'sort': 'newest', 'viewMode': 'table'},
        'admin_interactions': {'perPage': 20, 'sort': 'newest', 'viewMode': 'table'},
    },
    'display': {'sidebar_collapsed': False, 'theme': 'light'},
}
TABLES = frozenset(DEFAULT_PREFERENCES['pagination']) | {'interactions'}


def merge_with_defaults(defaults, user_prefs):
    result = deepcopy(defaults)
    if isinstance(user_prefs, dict):
        for key, value in user_prefs.items():
            result[key] = (merge_with_defaults(result[key], value)
                           if isinstance(result.get(key), dict) and isinstance(value, dict)
                           else deepcopy(value))
    return result


class PreferenceService(TenantService):
    def __init__(self, session, principal):
        super().__init__(session, principal)
        if self._query(User).filter(User.id == principal.user_id,
                                    User.is_active.is_(True)).first() is None:
            raise PermissionError('Active user required')

    def _preferences(self):
        # This table has no tenant_id; scope both owner and owner's tenant.
        return self._query(UserPreference).join(User, User.id == UserPreference.user_id).filter(
            User.tenant_id == self.principal.tenant_id,
            UserPreference.user_id == self.principal.user_id)

    def get(self):
        preferences = {}
        for pref in self._preferences().all():
            preferences.setdefault(pref.category, {})[pref.preference_key] = pref.preference_value
        return merge_with_defaults(DEFAULT_PREFERENCES, preferences)

    def update_pagination(self, table_name, data):
        if not isinstance(table_name, str) or table_name not in TABLES:
            raise ValueError('Invalid table name')
        if not isinstance(data, dict):
            raise ValueError('Invalid request body')
        per_page = data.get('perPage', 10)
        sort = data.get('sort', 'newest')
        view = data.get('viewMode', 'cards')
        if type(per_page) is not int or not 1 <= per_page <= 100:
            raise ValueError('Invalid perPage value (1-100)')
        if sort not in ('newest', 'oldest', 'alphabetical', 'pending', 'completed'):
            raise ValueError('Invalid sort order')
        if view not in ('cards', 'table'):
            view = 'cards'
        value = {'perPage': per_page, 'sort': sort, 'viewMode': view}
        pref = self._preferences().filter(UserPreference.category == 'pagination',
                                         UserPreference.preference_key == table_name).first()
        if pref is None:
            pref = UserPreference(user_id=self.principal.user_id, category='pagination',
                                  preference_key=table_name, preference_value=value)
            self.session.add(pref)
        else:
            pref.preference_value = value
            pref.updated_at = datetime.utcnow()
        self.session.flush()
        return {'message': f'Pagination preferences updated for {table_name}', 'preference': value}
