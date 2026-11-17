"""HTTP adapters for tenant-bound file operations."""
from io import BytesIO
from quart import Blueprint, current_app, jsonify, request, send_file
from sqlalchemy.exc import SQLAlchemyError
from app.database import SessionLocal
from app.services.errors import RecordNotFound
from app.services.storage import StorageService, UploadTooLarge
from app.utils.auth_utils import requires_auth
from app.utils.storage_backend import get_storage

storage_bp = Blueprint('storage', __name__, url_prefix='/api/storage')


async def _respond(operation):
    with SessionLocal() as session:
        try:
            service = StorageService(session, request.principal, get_storage(),
                local_root=current_app.config.get('STORAGE_ROOT', './storage'),
                max_size=current_app.config.get('MAX_CONTENT_LENGTH'))
            return await operation(service)
        except RecordNotFound as exc:
            return jsonify({'error': str(exc)}), 404
        except PermissionError as exc:
            return jsonify({'error': str(exc)}), 403
        except UploadTooLarge as exc:
            return jsonify({'error': str(exc)}), 413
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400
        except SQLAlchemyError:
            raise
        except Exception as exc:
            current_app.logger.error('Storage operation failed', extra={
                'exception_type': type(exc).__name__, 'tenant_id': request.principal.tenant_id})
            return jsonify({'error': 'Storage temporarily unavailable'}), 503


@storage_bp.route('/list', methods=['GET'])
@requires_auth()
async def list_files():
    async def operation(service):
        return jsonify(service.list_files())
    return await _respond(operation)


@storage_bp.route('/upload', methods=['POST'])
@requires_auth(roles=['file_uploads'])
async def upload_files():
    form = await request.files
    async def operation(service):
        return jsonify(await service.upload(form.getlist('files'))), 201
    return await _respond(operation)


@storage_bp.route('/download/<int:file_id>', methods=['GET'])
@requires_auth()
async def download_file(file_id):
    async def operation(service):
        data, filename, mimetype = await service.download(file_id)
        return await send_file(BytesIO(data), as_attachment=True,
                               attachment_filename=filename, mimetype=mimetype)
    return await _respond(operation)


@storage_bp.route('/delete/<int:file_id>', methods=['DELETE'])
@requires_auth(roles=['file_uploads'])
async def delete_file(file_id):
    async def operation(service):
        await service.delete(file_id)
        return jsonify({'message': 'Deleted'})
    return await _respond(operation)
