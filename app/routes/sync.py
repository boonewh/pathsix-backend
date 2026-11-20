"""Change feed for offline devices. Contract: docs/mobile-sync-feed.md."""
from quart import Blueprint, current_app, jsonify, request

from app.database import SessionLocal
from app.services.sync_feed import (DeviceRevoked, InvalidCursor, InvalidLimit, ResyncRequired,
                                    SyncFeedService, UnsupportedDatabase)
from app.utils.auth_utils import requires_auth

sync_bp = Blueprint('sync', __name__, url_prefix='/api/sync')


def _private(body, status=200):
    response = jsonify(body)
    response.status_code = status
    response.headers['Cache-Control'] = 'no-store'  # user-bound records
    return response


@sync_bp.route('/changes', methods=['GET'])
@requires_auth()
async def changes():
    # Removals are scoped to what this device holds (docs/mobile-device-auth.md).
    if not getattr(request, 'device_id', None):
        return _private({'code': 'device_token_required',
                         'error': 'The change feed needs a mobile device token'}, 403)
    # Each page records what the device was sent: never replay it automatically.
    request.database_write_started = True
    with SessionLocal() as session:
        try:
            result = SyncFeedService(session, request.principal).page(
                request.args.get('cursor') or None, request.args.get('limit'),
                secret=current_app.config['SECRET_KEY'], device_id=request.device_id, user=request.user)
            session.commit()
            return _private(result)
        except (InvalidCursor, InvalidLimit) as exc:
            session.rollback()
            return _private({'code': exc.code, 'error': str(exc)}, 400)
        except DeviceRevoked as exc:
            session.rollback()
            return _private({'code': exc.code, 'error': str(exc)}, 401)
        except ResyncRequired as exc:
            session.rollback()
            return _private({'code': exc.code, 'error': str(exc)}, 410)
        except UnsupportedDatabase as exc:
            session.rollback()
            return _private({'code': exc.code, 'error': str(exc)}, 501)
