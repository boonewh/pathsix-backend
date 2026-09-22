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

    @property
    def is_admin(self):
        return "admin" in self.roles
