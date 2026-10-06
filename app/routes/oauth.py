"""OAuth authorization-code/PKCE pilot for operator-registered HTTPS clients."""
import secrets
from datetime import datetime
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from quart import Blueprint, current_app, request, jsonify, render_template, make_response
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from authlib.oauth2.rfc6749.errors import OAuth2Error, InvalidRequestError
from app.database import SessionLocal
from app.models import OAuthCredential, AIConnection
from app.services.oauth import Server, ProtocolRequest, lookup_principal, issuer, digest, valid_secret
from app.services.ai_connections import SCOPES, resource_uri, AIConfigurationError
from app.services.database_context import bind_principal
from app.services.errors import RecordNotFound
from app.utils.auth_utils import requires_auth
from app.utils.rate_limiter import rate_limit

oauth_bp = Blueprint('oauth', __name__, template_folder='../templates', static_folder='../static', static_url_path='/oauth/assets')
COOKIE = '__Host-pathsix-oauth'
INTENT_SECONDS = 300


def signer():
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'], salt='oauth-browser-consent-v1')


def reply(body, status=200):
    response = jsonify(body)
    response.status_code = status
    return response


def restart_required(reason):
    # Browser-only guidance; never return the signed intent or request parameters.
    return reply({'error': 'invalid_request', 'reason': reason, 'restart_required': True}, 400)


@oauth_bp.before_request
async def limit_body():
    request.max_content_length = 8192


@oauth_bp.after_request
async def protect(response):
    response.headers.update({'Cache-Control':'no-store', 'Pragma':'no-cache',
        'Referrer-Policy':'no-referrer', 'X-Content-Type-Options':'nosniff', 'X-Frame-Options':'DENY',
        'Content-Security-Policy':"default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"})
    return response


@oauth_bp.errorhandler(OAuth2Error)
async def protocol_error(error):
    return reply({'error':error.error}, error.status_code)


@oauth_bp.errorhandler(AIConfigurationError)
async def unavailable(error):
    return reply({'error':'temporarily_unavailable'},503)


@oauth_bp.route('/.well-known/oauth-authorization-server')
async def metadata():
    base = issuer()
    return reply({'issuer':base, 'authorization_endpoint':base+'/oauth/authorize',
        'token_endpoint':base+'/oauth/token', 'revocation_endpoint':base+'/oauth/revoke',
        'response_types_supported':['code'], 'grant_types_supported':['authorization_code','refresh_token'],
        'token_endpoint_auth_methods_supported':['none'], 'revocation_endpoint_auth_methods_supported':['none'],
        'code_challenge_methods_supported':['S256'], 'scopes_supported':list(SCOPES),
        'authorization_response_iss_parameter_supported':True})


def single_values(values):
    if any(len(values.getlist(key)) != 1 for key in values):
        raise InvalidRequestError()
    return values.to_dict()


@oauth_bp.route('/oauth/connections')
async def connections():
    return await render_template('oauth_connections.html', staging=current_app.config.get('OAUTH_STAGING', False))


@oauth_bp.route('/oauth/authorize')
@rate_limit(max_attempts=30, window_seconds=60)
async def authorize():
    if len(request.query_string) > 4096: raise InvalidRequestError()
    data = single_values(request.args)
    with SessionLocal() as db:
        protocol, _ = Server(db).validate_authorization(data)
        name = protocol.client.row.name
    binding = secrets.token_urlsafe(32)
    intent = signer().dumps({'request':data,'binding':digest(binding),'nonce':secrets.token_urlsafe(16)})
    response = await make_response(await render_template('oauth.html', intent=intent,
        client_name=name, callback_host=urlsplit(data['redirect_uri']).netloc,
        permissions=[SCOPES[s] for s in data['scope'].split()], client_id=data['client_id'], scopes=data['scope'],
        intent_seconds=INTENT_SECONDS, staging=current_app.config.get('OAUTH_STAGING', False)))
    response.set_cookie(COOKIE,binding,max_age=INTENT_SECONDS,secure=True,httponly=True,samesite='Lax',path='/')
    return response


@oauth_bp.route('/oauth/decision',methods=['POST'])
@requires_auth()
async def decision():
    if request.headers.get('Origin') != issuer():
        raise InvalidRequestError()
    data = await request.get_json(silent=True)
    if not isinstance(data,dict) or set(data) != {'intent','approved'} or type(data['approved']) is not bool:
        raise InvalidRequestError()
    try:
        intent = signer().loads(data['intent'],max_age=INTENT_SECONDS)
        binding = request.cookies.get(COOKIE,'')
        if not valid_secret(binding) or not secrets.compare_digest(intent['binding'],digest(binding)):
            return restart_required('browser_mismatch')
    except SignatureExpired:
        return restart_required('expired_intent')
    except (BadSignature,ValueError,TypeError,KeyError):
        return restart_required('invalid_intent')
    request.database_write_started = True
    with SessionLocal() as db:
        try:
            server = Server(db,request.principal,intent_hash=digest(data['intent']))
            protocol, grant = server.validate_authorization(intent['request'])
            status,body,headers = server.create_authorization_response(protocol,
                grant_user=request.principal if data['approved'] else None,grant=grant)
            redirect = dict(headers).get('Location')
            if status != 302 or not redirect:
                db.rollback()
                return restart_required('connection_changed')
            db.commit()
        except IntegrityError:
            db.rollback()
            return restart_required('connection_changed')
        except OAuth2Error:
            db.rollback()
            return restart_required('connection_changed')
        except (PermissionError,ValueError,RecordNotFound):
            db.rollback()
            return reply({'error':'access_denied'},403)
    parts = urlsplit(redirect)
    redirect = urlunsplit(parts._replace(query=urlencode(parse_qsl(parts.query)+[('iss',issuer())])))
    response = reply({'redirect':redirect})
    response.delete_cookie(COOKIE,secure=True,httponly=True,samesite='Lax',path='/')
    return response


async def form_data():
    if (request.mimetype != 'application/x-www-form-urlencoded' or request.args
            or request.headers.get('Authorization') or (request.content_length or 0) > 4096):
        raise InvalidRequestError()
    return single_values(await request.form)


@oauth_bp.route('/oauth/token',methods=['POST'])
@rate_limit(max_attempts=60, window_seconds=60)
async def token():
    data = await form_data()
    grant_type = data.get('grant_type')
    required = {'grant_type','client_id','resource'}
    if grant_type == 'authorization_code': required |= {'code','code_verifier','redirect_uri'}
    elif grant_type == 'refresh_token': required |= {'refresh_token'}
    else: return reply({'error':'unsupported_grant_type'},400)
    optional = {'scope'} if grant_type == 'refresh_token' else set()
    if not required <= set(data) or set(data)-required-optional or data['resource'] != resource_uri():
        raise InvalidRequestError()
    raw = data.get('code') if grant_type=='authorization_code' else data.get('refresh_token')
    try:
        principal = lookup_principal(SessionLocal,raw)
        if principal is None: return reply({'error':'invalid_grant'},400)
        request.principal = principal
        request.database_write_started = True
        with SessionLocal() as db:
            server = Server(db,principal)
            status,body,headers = server.create_token_response(ProtocolRequest(data))
            # A recognized replay revokes the family even though exchange failed.
            db.commit()
        response = reply(body,status)
        response.headers.extend(headers)
        return response
    except SQLAlchemyError:
        # Do not log request bodies, token values, or driver parameters.
        return reply({'error':'temporarily_unavailable'},503)


@oauth_bp.route('/oauth/revoke',methods=['POST'])
@rate_limit(max_attempts=60, window_seconds=60)
async def revoke():
    data = await form_data()
    if not {'client_id','token'} <= set(data) or set(data)-{'client_id','token','token_type_hint'}:
        raise InvalidRequestError()
    try:
        principal = lookup_principal(SessionLocal,data['token'])
        if principal is None: return reply({})
        request.principal = principal
        request.database_write_started = True
        with SessionLocal() as db:
            bind_principal(db,principal)
            credential = db.query(OAuthCredential).filter_by(token_hash=digest(data['token']),
                user_id=principal.user_id,tenant_id=principal.tenant_id).filter(OAuthCredential.kind.in_(['access','refresh'])).first()
            if credential:
                connection = db.query(AIConnection).filter_by(id=credential.connection_id,
                    user_id=principal.user_id,tenant_id=principal.tenant_id,client_id=data['client_id']).with_for_update().first()
                if connection and connection.revoked_at is None: connection.revoked_at = datetime.utcnow()
            db.commit()
        return reply({})
    except SQLAlchemyError:
        return reply({'error':'temporarily_unavailable'},503)
