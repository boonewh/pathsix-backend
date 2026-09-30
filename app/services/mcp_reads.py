"""Only this boundary adapts verified delegated credentials to two pure CRM reads."""
from datetime import datetime, timedelta
from uuid import uuid4
from authlib.oauth2.rfc6749.errors import InvalidScopeError
from app.models import AIConnection, AIToolAudit
from app.services.oauth import lookup_principal, validate_access
from app.services.database_context import bind_principal
from app.services.ai_connections import resource_uri
from app.services.clients import ClientService
from app.services.errors import RecordNotFound

TOOLS = ('list_clients','get_client')
MAX_CALLS_PER_MINUTE = 60


def authenticate(factory, raw):
    principal = lookup_principal(factory,raw)
    with factory() as db:
        identity = validate_access(db,principal,raw,resource_uri())
    return principal,identity


def execute(factory, raw, name, arguments):
    # No request-supplied owner, tenant, role, scope or Principal is accepted.
    principal,identity = authenticate(factory,raw)
    with factory() as db:
        bind_principal(db,principal)
        grant = db.query(AIConnection).filter_by(id=identity.connection_id,
            tenant_id=principal.tenant_id,user_id=principal.user_id).with_for_update().one()
        validate_access(db,principal,raw,resource_uri())  # Recheck after waiting for revocation's lock.
        now = datetime.utcnow()
        recent = db.query(AIToolAudit).filter_by(connection_id=grant.id,
            tenant_id=principal.tenant_id,user_id=principal.user_id).filter(
                AIToolAudit.created_at > now-timedelta(minutes=1)).count()
        if recent >= MAX_CALLS_PER_MINUTE:
            return None,'Too many tool calls. Try again later.'
        result, error, outcome = None,None,'success'
        try:
            if name not in TOOLS:
                outcome,error = 'unknown_tool','Unknown tool'
            else:
                validate_access(db,principal,raw,resource_uri(),['clients:read'])
                if not isinstance(arguments,dict): raise ValueError()
                service = ClientService(db,principal)
                if name == 'list_clients':
                    if set(arguments)-{'after_id','limit'}: raise ValueError()
                    result = service.summaries(**arguments)
                else:
                    if set(arguments) != {'client_id'} or arguments['client_id'] is None: raise ValueError()
                    result = {'client':service.summaries(client_id=arguments['client_id'],limit=1)['clients'][0]}
        except (InvalidScopeError,PermissionError):
            outcome,error = 'forbidden','Required permission is not available'
        except ValueError:
            outcome,error = 'invalid_arguments','Invalid tool arguments'
        except RecordNotFound:
            outcome,error = 'not_found','Client not found'
        count = 0 if result is None else len(result['clients']) if name=='list_clients' else 1
        db.add(AIToolAudit(id=str(uuid4()),tenant_id=principal.tenant_id,user_id=principal.user_id,
            connection_id=grant.id,tool=name if name in TOOLS else 'unknown',outcome=outcome,
            result_count=count,created_at=now))
        if outcome == 'success': grant.last_used_at = now
        # Audit and last-use must persist before any CRM data is returned.
        db.commit()
        return result,error
