"""CRM-authenticated human review; delegated MCP credentials cannot approve actions."""
import secrets
from itsdangerous import URLSafeTimedSerializer, BadSignature
from quart import Blueprint, request, current_app, render_template, make_response
from sqlalchemy.exc import SQLAlchemyError

from app.database import SessionLocal
from app.routes.oauth import protect, reply
from app.services.ai_actions import AIActionService, ActionConflict, payload_hash
from app.services.ai_connections import AIConfigurationError
from app.services.errors import RecordNotFound
from app.services.oauth import issuer, digest, valid_secret
from app.utils.auth_utils import requires_auth
from app.utils.rate_limiter import rate_limit

ai_actions_bp = Blueprint('ai_actions', __name__, template_folder='../templates')
COOKIE = '__Host-pathsix-action'
REVIEW_SECONDS = 300


def signer():
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'], salt='ai-action-review-v1')


ai_actions_bp.after_request(protect)


@ai_actions_bp.before_request
async def limit_body():
    request.max_content_length = 8192


@ai_actions_bp.errorhandler(SQLAlchemyError)
@ai_actions_bp.errorhandler(AIConfigurationError)
async def unavailable(error):
    return reply({'error': 'Action temporarily unavailable. Refresh its status before trying again.'}, 503)


@ai_actions_bp.route('/oauth/actions/<ident>')
@rate_limit(max_attempts=30, window_seconds=60)
async def page(ident):
    # Public shell contains no proposal data. Login is required before review.
    response = await make_response(await render_template('ai_action.html',
        action_id=ident, staging=current_app.config.get('OAUTH_STAGING', False)))
    response.set_cookie(COOKIE, secrets.token_urlsafe(32), max_age=600,
        secure=True, httponly=True, samesite='Strict', path='/')
    return response


def browser_binding():
    value = request.cookies.get(COOKIE, '')
    if not valid_secret(value):
        raise ValueError('Reopen the review page')
    return digest(value)


def error_response(error):
    status = 404 if isinstance(error, RecordNotFound) else 403 if isinstance(error, PermissionError) else 409 if isinstance(error, ActionConflict) else 400
    return reply({'error': str(error)}, status)


@ai_actions_bp.route('/oauth/actions/<ident>/preview', methods=['POST'])
@requires_auth()
async def preview(ident):
    if request.headers.get('Origin') != issuer():
        return reply({'error': 'Invalid origin'}, 403)
    with SessionLocal() as db:
        try:
            row, result = AIActionService(db, request.principal).preview(ident)
            if result['status'] == 'pending':
                result['intent'] = signer().dumps({'id': row.id,
                    'user': request.principal.user_id, 'tenant': request.principal.tenant_id,
                    'hash': payload_hash(row.payload), 'binding': browser_binding()})
            return reply(result)
        except (RecordNotFound, PermissionError, ValueError) as error:
            return error_response(error)


@ai_actions_bp.route('/oauth/actions/<ident>/decision', methods=['POST'])
@requires_auth()
async def decision(ident):
    if request.headers.get('Origin') != issuer():
        return reply({'error': 'Invalid origin'}, 403)
    data = await request.get_json(silent=True)
    try:
        if not isinstance(data, dict) or set(data) != {'intent', 'approved'} or type(data['approved']) is not bool:
            raise ValueError('Explicit review and decision required')
        intent = signer().loads(data['intent'], max_age=REVIEW_SECONDS)
        if (intent['id'] != ident or intent['user'] != request.principal.user_id
                or intent['tenant'] != request.principal.tenant_id
                or not secrets.compare_digest(intent['binding'], browser_binding())):
            raise ValueError('Review this proposal again')
    except (BadSignature, KeyError, TypeError, ValueError):
        return reply({'error': 'Review this proposal again before confirming'}, 400)
    request.database_write_started = True
    with SessionLocal() as db:
        try:
            result = AIActionService(db, request.principal).decide(ident, data['approved'], intent['hash'])
            db.commit()
            return reply(result)
        except (RecordNotFound, PermissionError, ValueError) as error:
            db.rollback()
            return error_response(error)
        except SQLAlchemyError:
            db.rollback()
            return reply({'error': 'Action temporarily unavailable. Refresh its status before trying again.'}, 503)
