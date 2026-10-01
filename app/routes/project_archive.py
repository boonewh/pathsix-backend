"""Direct-link private archive. No ordinary admin role confers access."""
from quart import Blueprint, request, jsonify, current_app
from app.database import SessionLocal
from app.services.project_archive import ProjectArchiveService, ArchiveConflict
from app.utils.project_archive_access import requires_archive_owner

project_archive_bp = Blueprint('project_archive', __name__, url_prefix='/api/owner/project-archive')


@project_archive_bp.after_request
async def no_cache(response):
    response.headers['Cache-Control'] = 'no-store, private'
    return response


def service(session):
    return ProjectArchiveService(session, request.user.id, current_app.config['SECRET_KEY'])


@project_archive_bp.get('')
@requires_archive_owner
async def list_archive():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 25, type=int)
    if not page or page < 1 or not per_page or not 1 <= per_page <= 100:
        return jsonify({'error': 'Invalid pagination'}), 400
    with SessionLocal() as session:
        return jsonify(service(session).listing(page, per_page, request.args.get('search', '')[:200]))


@project_archive_bp.get('/preview')
@requires_archive_owner
async def preview_archive():
    with SessionLocal() as session:
        return jsonify(service(session).preview())


@project_archive_bp.post('/archive')
@requires_archive_owner
async def archive_projects():
    data = await request.get_json()
    if not isinstance(data, dict):
        return jsonify({'error': 'Invalid request body'}), 400
    with SessionLocal() as session:
        try:
            result = service(session).archive(data.get('preview_token'), data.get('confirmation'))
            session.commit()
            return jsonify(result)
        except ArchiveConflict as exc:
            session.rollback()
            return jsonify({'error': str(exc)}), 409
        except ValueError as exc:
            session.rollback()
            return jsonify({'error': str(exc)}), 400


@project_archive_bp.get('/<int:project_id>')
@requires_archive_owner
async def archive_detail(project_id):
    with SessionLocal() as session:
        result = service(session).detail(project_id)
        return (jsonify(result), 200) if result else (jsonify({'error': 'Project not found'}), 404)


@project_archive_bp.post('/<int:project_id>/restore')
@requires_archive_owner
async def restore_project(project_id):
    with SessionLocal() as session:
        result = service(session).restore(project_id)
        if result is None:
            return jsonify({'error': 'Project not found'}), 404
        session.commit()
        return jsonify(result)
