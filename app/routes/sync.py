"""Change feed for offline devices. Contract: docs/mobile-sync-feed.md."""
from quart import Blueprint, current_app, jsonify, request

from app.database import SessionLocal
from app.services.sync_feed import (InvalidCursor, InvalidLimit, ResyncRequired, SyncFeedService,
                                    UnsupportedDatabase)
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
    with SessionLocal() as session:
        try:
            return _private(SyncFeedService(session, request.principal).page(
                request.args.get('cursor') or None, request.args.get('limit'),
                secret=current_app.config['SECRET_KEY']))
        except (InvalidCursor, InvalidLimit) as exc:
            return _private({'code': exc.code, 'error': str(exc)}, 400)
        except ResyncRequired as exc:
            return _private({'code': exc.code, 'error': str(exc)}, 410)
        except UnsupportedDatabase as exc:
            return _private({'code': exc.code, 'error': str(exc)}, 501)
        finally:
            # Read-only: never leave the feed's transaction open or committed.
            session.rollback()
