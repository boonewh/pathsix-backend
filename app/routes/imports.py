"""HTTP and post-commit email adapters for tenant-bound lead imports."""
import json
from quart import Blueprint, Response, current_app, jsonify, request
from sqlalchemy.exc import SQLAlchemyError
from app.database import SessionLocal
from app.services.imports import ImportService
from app.utils.auth_utils import requires_auth
from app.utils.email_utils import send_email

imports_bp = Blueprint('imports', __name__, url_prefix='/api/import')


async def _respond(operation):
    with SessionLocal() as session:
        try:
            return await operation(ImportService(session, request.principal), session)
        except PermissionError as exc:
            session.rollback()
            return jsonify({'error': str(exc)}), 403
        except ValueError as exc:
            session.rollback()
            return jsonify({'error': str(exc)}), 400
        except SQLAlchemyError:
            session.rollback()
            raise
        except Exception as exc:
            session.rollback()
            current_app.logger.error('Lead import failed', extra={'exception_type': type(exc).__name__})
            return jsonify({'error': 'Import could not be completed'}), 500


@imports_bp.route('/leads/preview', methods=['POST'])
@requires_auth(roles=['admin'])
async def preview_leads():
    files = await request.files
    async def operation(service, session):
        return jsonify(service.preview(files.get('file')))
    return await _respond(operation)


@imports_bp.route('/leads/submit', methods=['POST'])
@requires_auth(roles=['admin'])
async def submit_leads():
    form, files = await request.form, await request.files
    async def operation(service, session):
        try:
            mappings = json.loads(form.get('column_mappings', '[]'))
        except (ValueError, TypeError):
            raise ValueError('Invalid column mappings') from None
        result = service.submit(files.get('file'), mappings, form.get('assigned_user_email'))
        if result.response['successful_imports']:
            session.commit()
        if result.notification:
            try:
                await send_email(**result.notification)
            except Exception as exc:
                current_app.logger.warning('Import summary email failed after commit',
                    extra={'exception_type': type(exc).__name__})
        return jsonify(result.response)
    return await _respond(operation)


@imports_bp.route('/leads/template', methods=['GET'])
@requires_auth(roles=['admin'])
async def get_lead_template():
    async def operation(service, session):
        return Response(service.template(), mimetype='text/csv',
            headers={'Content-Disposition': 'attachment;filename=lead_import_template.csv'})
    return await _respond(operation)
