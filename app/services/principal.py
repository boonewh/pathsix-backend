from dataclasses import dataclass


@dataclass(frozen=True)
class Principal:
    """One authenticated operation's identity; never construct from request data.

    The auth boundary supplies current database identity/roles on every request.
    Future OAuth adapters must also validate grants/scopes before creating context.
    Do not cache this snapshot across requests or use it as a delegated AI token.
    """
    user_id: int
    tenant_id: int
    roles: frozenset[str]
    connection_type: str = "web"

    def __post_init__(self):
        if type(self.user_id) is not int or self.user_id < 1 or type(self.tenant_id) is not int or self.tenant_id < 1:
            raise ValueError('Positive integer user and tenant identities are required')
        if not isinstance(self.roles, (set, frozenset, list, tuple)) or any(
                not isinstance(role, str) or not role for role in self.roles):
            raise ValueError('Current role names are required')
        object.__setattr__(self, 'roles', frozenset(self.roles))
        # Delegated connection/grant validation must exist before another adapter
        # can supply an identity. A type label alone must never enable MCP access.
        if self.connection_type != 'web':
            raise ValueError('Unsupported authentication boundary')

    @property
    def is_admin(self):
        return "admin" in self.roles
