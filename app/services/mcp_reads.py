"""Verified delegated tool boundary; capability-bound UI confirmation."""
from datetime import datetime, timedelta
from uuid import uuid4
from authlib.oauth2.rfc6749.errors import InvalidScopeError
from app.models import AIConnection, AIToolAudit
from app.services.oauth import lookup_principal, validate_access
from app.services.database_context import bind_principal
from app.services.ai_connections import resource_uri
from app.services.clients import ClientService
from app.services.errors import RecordNotFound
from app.services.leads import LeadService
from app.services.ai_actions import AIActionService, ActionConflict, REQUIRED_SCOPES
from app.utils.lead_options import tenant_lead_config, configured_options

TOOL_SCOPES = {
    'list_clients': ['clients:read'], 'get_client': ['clients:read'],
    'list_leads': ['leads:read'], 'get_lead': ['leads:read'], 'get_lead_options': ['leads:read'],
    'prepare_lead_creation': REQUIRED_SCOPES, 'get_lead_creation': REQUIRED_SCOPES,
    'decide_lead_creation': REQUIRED_SCOPES,
}
TOOLS = tuple(TOOL_SCOPES)
MAX_CALLS_PER_MINUTE = 60


def authenticate(factory, raw):
    principal = lookup_principal(factory,raw)
    with factory() as db:
        identity = validate_access(db,principal,raw,resource_uri())
    return principal,identity


def execute(factory, raw, name, arguments, *, review=None):
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
                validate_access(db,principal,raw,resource_uri(),TOOL_SCOPES[name])
                if not isinstance(arguments,dict): raise ValueError()
                result = dispatch(db, principal, identity.connection_id, name, arguments, review=review)
        except (InvalidScopeError,PermissionError):
            outcome,error = 'forbidden','Required permission is not available'
        except ActionConflict as conflict:
            outcome,error = 'invalid_arguments',str(conflict)
        except ValueError:
            outcome,error = 'invalid_arguments','Invalid tool arguments'
        except RecordNotFound:
            outcome,error = 'not_found','Client not found' if name in ('get_client','list_clients') else 'Record or action not found'
        if error:
            db.rollback()
            grant = db.query(AIConnection).filter_by(id=identity.connection_id,
                tenant_id=principal.tenant_id,user_id=principal.user_id).with_for_update().one()
            validate_access(db,principal,raw,resource_uri())
        count = 0 if result is None else len(result.get('clients', result.get('leads', [result])))
        db.add(AIToolAudit(id=str(uuid4()),tenant_id=principal.tenant_id,user_id=principal.user_id,
            connection_id=grant.id,tool=name if name in TOOLS else 'unknown',outcome=outcome,
            result_count=count,created_at=now))
        if outcome == 'success': grant.last_used_at = now
        # Audit and last-use must persist before any CRM data is returned.
        db.commit()
        return result,error


def dispatch(db, principal, connection_id, name, args, *, review=None):
    if name == 'list_clients':
        if set(args)-{'after_id','limit'}: raise ValueError()
        return ClientService(db,principal).summaries(**args)
    if name == 'get_client':
        if set(args) != {'client_id'} or args['client_id'] is None: raise ValueError()
        return {'client':ClientService(db,principal).summaries(client_id=args['client_id'],limit=1)['clients'][0]}
    if name == 'list_leads':
        if set(args)-{'after_id','limit','query'}: raise ValueError()
        return LeadService(db,principal).summaries(**args)
    if name == 'get_lead':
        if set(args) != {'lead_id'} or args['lead_id'] is None: raise ValueError()
        return {'lead':LeadService(db,principal).summaries(lead_id=args['lead_id'],limit=1)['leads'][0]}
    if name == 'get_lead_options':
        if args: raise ValueError()
        config = tenant_lead_config(db, principal.tenant_id)
        leads = config.get('leads')
        statuses = configured_options(leads.get('statuses')) if isinstance(leads,dict) else []
        return {'statuses':[v[:100] for v in statuses[:50]],
                'business_types':[v[:100] for v in configured_options(config.get('businessTypes'))[:50]],
                'default_status':(statuses[0] if statuses else 'open')[:100]}
    service = AIActionService(db,principal)
    if name == 'prepare_lead_creation':
        if set(args) != {'request_key','lead'}: raise ValueError()
        receipt = service.prepare(connection_id,args['request_key'],args['lead'])
        return review.present(service, connection_id, receipt) if review else receipt
    if name == 'get_lead_creation':
        if set(args) != {'action_id'}: raise ValueError()
        receipt = service.result(service.action(args['action_id'],connection_id=connection_id))
        return review.present(service, connection_id, receipt) if review else receipt
    if name == 'decide_lead_creation':
        if review is None:
            raise PermissionError('Component review is unavailable')
        return review.decide(service, connection_id, args)
    raise ValueError()
