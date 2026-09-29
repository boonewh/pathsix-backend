"""HTTP adapter for current-user preferences."""
from quart import Blueprint, request, jsonify
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.database import SessionLocal
from app.services.preferences import PreferenceService
from app.utils.auth_utils import requires_auth

preferences_bp = Blueprint('preferences', __name__, url_prefix='/api/preferences')


def _respond(operation, *, write=False):
    with SessionLocal() as session:
        try:
            if write:
                request.database_write_started = True
            result = operation(PreferenceService(session, request.principal))
            if write:
                session.commit()
            response = jsonify(result)
            response.headers['Cache-Control'] = 'no-store'
            return response
        except PermissionError as exc:
            session.rollback()
            return jsonify({'error': str(exc)}), 403
        except ValueError as exc:
            session.rollback()
            return jsonify({'error': str(exc)}), 400
        except IntegrityError:
            session.rollback()
            return jsonify({'error': 'Preferences changed concurrently. Please try again.'}), 409
        except SQLAlchemyError:
            session.rollback()
            raise


@preferences_bp.route('', methods=['GET'])
@preferences_bp.route('/', methods=['GET'])
@requires_auth()
async def get_user_preferences():
    return _respond(lambda service: service.get())


@preferences_bp.route('/pagination/<table_name>', methods=['PUT'])
@requires_auth()
async def update_pagination_preference(table_name):
    data = await request.get_json()
    return _respond(lambda service: service.update_pagination(table_name, data), write=True)
