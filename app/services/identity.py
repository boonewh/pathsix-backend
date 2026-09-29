"""Current-user identity/configuration and caller-owned password changes."""
from copy import deepcopy

from sqlalchemy.orm import joinedload
from app.models import Tenant, User
from app.services.base import TenantService
from app.utils.auth_utils import hash_password, verify_password


class IdentityService(TenantService):
    def _user(self, *, lock=False):
        query = self._query(User).join(Tenant, Tenant.id == User.tenant_id).filter(
            User.id == self.principal.user_id, User.is_active.is_(True),
            Tenant.id == self.principal.tenant_id, Tenant.is_active.is_(True))
        # Reload current state even if this session already loaded the identity.
        query = query.populate_existing()
        if lock:
            query = query.with_for_update(of=User)
        else:
            query = query.options(joinedload(User.roles), joinedload(User.tenant))
        user = query.first()
        if user is None:
            raise PermissionError('Active user and tenant required')
        return user

    @staticmethod
    def _tenant(tenant):
        return {'id': tenant.id, 'name': tenant.name, 'slug': tenant.slug,
                'config': deepcopy(tenant.config)}

    def me(self):
        user = self._user()
        return {'id': user.id, 'email': user.email,
                'roles': [role.name for role in user.roles],
                'tenant_id': user.tenant_id, 'tenant': self._tenant(user.tenant)}

    def tenant_config(self):
        return self._tenant(self._user().tenant)

    def change_password(self, data):
        if not isinstance(data, dict):
            raise ValueError('Invalid request body')
        current, new = data.get('current_password'), data.get('new_password')
        if not isinstance(current, str) or not current or not isinstance(new, str) or not new:
            raise ValueError('Missing required fields')
        if len(new.encode('utf-8')) > 72:
            raise ValueError('New password must not exceed 72 UTF-8 bytes')
        user = self._user(lock=True)
        try:
            matches = verify_password(current, user.password_hash)
        except ValueError:
            matches = False
        if not matches:
            raise PermissionError('Incorrect current password')
        user.password_hash = hash_password(new)
        self.session.flush()
        return {'message': 'Password changed successfully'}
