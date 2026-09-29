"""HTTP and delivery adapters for tenant-bound user administration."""
from quart import Blueprint, request, jsonify, current_app
from sqlalchemy.exc import SQLAlchemyError

from app.database import SessionLocal
from app.services.errors import RecordNotFound
from app.services.users import UserService
from app.utils.auth_utils import requires_auth
from app.utils.email_utils import send_password_reset_email

users_bp = Blueprint('users', __name__, url_prefix='/api/users')


def _respond(operation, *, write=False, status=200):
    with SessionLocal() as session:
        try:
            if write:
                request.database_write_started = True
            result = operation(UserService(session, request.principal))
            if write:
                session.commit()
            response = jsonify(result)
            response.headers['Cache-Control'] = 'no-store'
            return response, status
        except RecordNotFound as exc:
            session.rollback()
            return jsonify({'error': str(exc)}), 404
        except PermissionError as exc:
            session.rollback()
            return jsonify({'error': str(exc)}), 403
        except ValueError as exc:
            session.rollback()
            return jsonify({'error': str(exc)}), 400
        except SQLAlchemyError:
            session.rollback()
            raise


@users_bp.route('', methods=['GET'])
@users_bp.route('/', methods=['GET'])
@requires_auth(roles=['admin'])
async def list_users():
    return _respond(lambda service: service.list())


@users_bp.route('', methods=['POST'])
@users_bp.route('/', methods=['POST'])
@requires_auth(roles=['admin'])
async def create_user():
    data = await request.get_json()
    return _respond(lambda service: service.create(data), write=True, status=201)


@users_bp.route('/<int:user_id>/toggle-active', methods=['PUT'])
@requires_auth(roles=['admin'])
async def toggle_user_active(user_id):
    return _respond(lambda service: service.toggle_active(user_id), write=True)


@users_bp.route('/<int:user_id>/roles', methods=['PUT'])
@requires_auth(roles=['admin'])
async def update_user_roles(user_id):
    data = await request.get_json()
    return _respond(lambda service: service.update_roles(user_id, data), write=True)


@users_bp.route('/<int:user_id>', methods=['PUT'])
@requires_auth(roles=['admin'])
async def update_user_email(user_id):
    data = await request.get_json()
    return _respond(lambda service: service.update_email(user_id, data), write=True)


@users_bp.route('/<int:user_id>/send-password-reset', methods=['POST'])
@requires_auth(roles=['admin'])
async def send_user_password_reset(user_id):
    recipient = []
    def prepare(service):
        recipient.append(service.password_reset_recipient(user_id))
        return {'message': 'Password reset email sent'}
    response, status = _respond(prepare)
    if status != 200:
        return response, status
    # The authorized lookup session closes before network delivery starts.
    try:
        await send_password_reset_email(recipient[0])
    except Exception as exc:
        current_app.logger.warning('Admin password reset email delivery failed',
                                   extra={'exception_type': type(exc).__name__})
        return jsonify({'error': 'Unable to send the password reset email right now. Please try again later.'}), 503
    return response, status
