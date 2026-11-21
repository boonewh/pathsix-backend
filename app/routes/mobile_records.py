"""Mobile record creates and conditional edits. Contract: docs/mobile-writes.md."""
import uuid as uuid_lib

from quart import Blueprint, jsonify, request
from sqlalchemy.exc import DBAPIError

from app.database import SessionLocal
from app.services.errors import RecordNotFound
from app.services.idempotency import mobile_write
from app.services.mobile_records import (EditConflict, InvalidMobileWrite, MobileRecordService, RefUnavailable,
                                         check_create, check_edit)
from app.utils.auth_utils import requires_auth

mobile_records_bp = Blueprint('mobile_records', __name__, url_prefix='/api/mobile/records')


def _private(body, status):
    response = jsonify(body)
    response.status_code = status
    response.headers['Cache-Control'] = 'no-store'
    return response


def _device_required():
    if not getattr(request, 'device_id', None):
        return _private({'code': 'device_token_required',
                         'error': 'These routes need a mobile device token'}, 403)
    return None


def _run(session, perform):
    try:
        return perform()
    except RecordNotFound as exc:
        session.rollback()
        return _private({'code': 'not_found', 'error': str(exc)}, 404)
    except RefUnavailable as exc:
        session.rollback()
        return _private({'code': exc.code, 'error': str(exc)}, 409)
    except EditConflict as exc:
        session.rollback()
        return _private({'code': exc.code, 'error': str(exc), 'conflicts': exc.conflicts,
                         'record': exc.record}, 409)
    except ValueError as exc:  # service validation, or a missing or malformed key
        session.rollback()
        return _private({'code': getattr(exc, 'code', 'invalid_request'), 'error': str(exc)}, 400)
    except DBAPIError as exc:
        # A deadlock, serialization failure or lock timeout with another request:
        # nothing was written; the phone retries the same request later.
        if getattr(getattr(exc, 'orig', None), 'pgcode', None) not in ('40P01', '40001', '55P03'):
            raise
        session.rollback()
        response = _private({'code': 'busy_retry', 'error': 'PathSix was busy; try again shortly'}, 503)
        response.headers['Retry-After'] = '1'
        return response


@mobile_records_bp.route('/<entity>', methods=['POST'])
@requires_auth()
async def create_record(entity):
    denied = _device_required()
    if denied:
        return denied
    body = await request.get_json(silent=True)
    try:
        ref, fields, parents = check_create(entity, body)
    except RecordNotFound as exc:
        return _private({'code': 'not_found', 'error': str(exc)}, 404)
    except InvalidMobileWrite as exc:
        return _private({'code': exc.code, 'error': str(exc)}, 400)
    request.database_write_started = True
    with SessionLocal() as session:
        return _run(session, lambda: mobile_write(
            session, request.principal, body,
            lambda: MobileRecordService(session, request.principal).create(entity, ref, fields, parents), 201))


@mobile_records_bp.route('/<entity>/<record_uuid>', methods=['PATCH'])
@requires_auth()
async def edit_record(entity, record_uuid):
    denied = _device_required()
    if denied:
        return denied
    body = await request.get_json(silent=True)
    try:
        target = uuid_lib.UUID(record_uuid)
        changes, base = check_edit(entity, body)
    except RecordNotFound as exc:
        return _private({'code': 'not_found', 'error': str(exc)}, 404)
    except InvalidMobileWrite as exc:
        return _private({'code': exc.code, 'error': str(exc)}, 400)
    except ValueError:
        return _private({'code': 'not_found', 'error': 'Record not found'}, 404)
    request.database_write_started = True
    with SessionLocal() as session:
        return _run(session, lambda: mobile_write(
            session, request.principal, body,
            lambda: MobileRecordService(session, request.principal).edit(entity, target, changes, base)))
