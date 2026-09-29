"""CRM-authenticated consent management; no OAuth token or MCP endpoint."""
from quart import Blueprint, request, jsonify
from sqlalchemy.exc import IntegrityError
from app.database import SessionLocal
from app.services.ai_connections import AIConnectionService, AIConfigurationError, ConnectionLimit
from app.services.errors import RecordNotFound
from app.utils.auth_utils import requires_auth

ai_connections_bp = Blueprint('ai_connections', __name__, url_prefix='/api/ai-connections')


def respond(operation, *, write=False, status=200):
    with SessionLocal() as session:
        try:
            result = operation(AIConnectionService(session, request.principal))
            if write:
                session.commit()
        except (RecordNotFound, PermissionError, ConnectionLimit, ValueError, AIConfigurationError, IntegrityError) as exc:
            session.rollback()
            status = (404 if isinstance(exc, RecordNotFound) else 403 if isinstance(exc, PermissionError)
                      else 409 if isinstance(exc, (ConnectionLimit, IntegrityError))
                      else 503 if isinstance(exc, AIConfigurationError) else 400)
            result = {'error': 'AI connection changed concurrently. Please try again.'
                      if isinstance(exc, IntegrityError) else str(exc)}
        response = jsonify(result)
        response.status_code = status
        response.headers['Cache-Control'] = 'no-store'
        return response


@ai_connections_bp.route('', methods=['GET'])
@requires_auth()
async def list_connections():
    def operation(service):
        return service.list(int(request.args.get('page', '1')), int(request.args.get('per_page', '20')))
    return respond(operation)


@ai_connections_bp.route('/clients', methods=['GET'])
@requires_auth()
async def available_clients():
    return respond(lambda service: service.catalog())


@ai_connections_bp.route('/preview', methods=['POST'])
@requires_auth()
async def preview_consent():
    data = await request.get_json()
    return respond(lambda service: service.preview(data))


@ai_connections_bp.route('/consent', methods=['POST'])
@requires_auth()
async def record_consent():
    data = await request.get_json()
    request.database_write_started = True
    return respond(lambda service: service.consent(data), write=True, status=201)


@ai_connections_bp.route('/<ident>', methods=['DELETE'])
@requires_auth()
async def revoke_connection(ident):
    request.database_write_started = True
    return respond(lambda service: service.revoke(ident), write=True)
