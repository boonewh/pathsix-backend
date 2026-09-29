"""Tenant-bound user administration; adapters own commits and email delivery."""
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app.models import Role, User
from app.services.base import TenantService
from app.services.errors import RecordNotFound
from app.utils.auth_utils import hash_password


class UserService(TenantService):
    def __init__(self, session, principal):
        super().__init__(session, principal)
        self._require_admin()

    @staticmethod
    def _body(data):
        if not isinstance(data, dict):
            raise ValueError('Invalid request body')
        return data

    @staticmethod
    def _email(value):
        if (not isinstance(value, str) or not value.strip()
                or len(value.strip()) > 120 or value.count('@') != 1
                or any(c.isspace() for c in value.strip())
                or not all(value.strip().split('@'))):
            raise ValueError('A valid email is required')
        return value.strip()

    def _roles(self, names):
        if (not isinstance(names, list)
                or any(not isinstance(name, str) or not name for name in names)):
            raise ValueError('Invalid roles')
        # Roles are a global catalog; membership belongs to a tenant-scoped user.
        roles = self._query(Role).filter(Role.name.in_(names)).all()
        if {role.name for role in roles} != set(names):
            raise ValueError('One or more roles not found')
        return roles

    def _target(self, user_id):
        if type(user_id) is not int or user_id < 1:
            raise RecordNotFound('User not found')
        target = self._query(User).options(joinedload(User.roles)).filter(User.id == user_id).first()
        if target is None:
            raise RecordNotFound('User not found')
        return target

    @staticmethod
    def _serialize(user):
        return {'id': user.id, 'email': user.email,
                'roles': [role.name for role in user.roles],
                'is_active': user.is_active,
                'created_at': user.created_at.isoformat() + 'Z'}

    def list(self):
        return [self._serialize(user) for user in
                self._query(User).options(joinedload(User.roles)).all()]

    def _check_email(self, email, exclude_id=None):
        query = self._query(User).filter(User.email == email)
        if exclude_id is not None:
            query = query.filter(User.id != exclude_id)
        if query.first() is not None:
            raise ValueError('Email is unavailable')

    def _flush_email(self):
        try:
            self.session.flush()
        except IntegrityError as exc:
            # Global uniqueness may collide with an RLS-hidden account or a
            # concurrent insert. Return the same safe error as a visible collision.
            original = exc.orig
            code = getattr(original, 'sqlstate', None) or getattr(original, 'pgcode', None)
            constraint = getattr(getattr(original, 'diag', None), 'constraint_name', '') or ''
            sqlite_email = (self.session.bind.dialect.name == 'sqlite'
                            and 'UNIQUE constraint failed: users.email' in str(original))
            if sqlite_email or (code == '23505' and constraint in ('users_email_key', 'ix_users_email')):
                raise ValueError('Email is unavailable') from None
            raise

    def create(self, data):
        data = self._body(data)
        email = self._email(data.get('email'))
        password = data.get('password')
        if not isinstance(password, str) or not password or len(password.encode('utf-8')) > 72:
            raise ValueError('Password must contain between 1 and 72 UTF-8 bytes')
        roles = self._roles(data.get('roles', []))
        self._check_email(email)
        user = User(tenant_id=self.principal.tenant_id, email=email,
                    password_hash=hash_password(password), is_active=True, roles=roles)
        self.session.add(user)
        self._flush_email()
        return self._serialize(user)

    def toggle_active(self, user_id):
        target = self._target(user_id)
        if target.id == self.principal.user_id:
            raise PermissionError('You cannot deactivate yourself')
        target.is_active = not target.is_active
        self.session.flush()
        return {'id': target.id, 'is_active': target.is_active}

    def update_roles(self, user_id, data):
        target = self._target(user_id)
        roles = self._roles(self._body(data).get('roles', []))
        target.roles = roles
        self.session.flush()
        return {'id': target.id, 'roles': [role.name for role in target.roles]}

    def update_email(self, user_id, data):
        target = self._target(user_id)
        email = self._email(self._body(data).get('email'))
        self._check_email(email, target.id)
        target.email = email
        self._flush_email()
        return self._serialize(target)

    def password_reset_recipient(self, user_id):
        return self._target(user_id).email
