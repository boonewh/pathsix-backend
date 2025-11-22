# app/routes/storage.py
import os
import uuid
from datetime import datetime
from quart import Blueprint, request, jsonify, send_file, Response, current_app
from sqlalchemy.exc import SQLAlchemyError
from app.database import SessionLocal
from app.models import File
from app.utils.auth_utils import requires_auth
from app.utils.storage_backend import get_storage
import inspect



storage_bp = Blueprint("storage", __name__, url_prefix="/api/storage")

def _tenant_key(tenant_id: int, stored_name: str) -> str:
    # consistent key prefix for all vendors
    return f"tenant-{tenant_id}/{stored_name}"

@storage_bp.route("/list", methods=["GET"])
@requires_auth()
async def list_files():
    user = request.user
    session = SessionLocal()
    try:
        files = (
            session.query(File)
            .filter(File.tenant_id == user.tenant_id)
            .order_by(File.uploaded_at.desc())
            .all()
        )
        return jsonify([f.to_dict() for f in files])
    finally:
        session.close()

@storage_bp.route("/upload", methods=["POST"])
@requires_auth(roles=["file_uploads"])
async def upload_files():
    user = request.user
    form = await request.files

    # Support multiple files under the same key "files"
    items = form.getlist("files") if hasattr(form, "getlist") else list(form.values())
    if not items:
        return jsonify({"error": "No files uploaded"}), 400

    max_size = current_app.config.get("MAX_CONTENT_LENGTH", 20 * 1024 * 1024)  # 20MB default
    storage = get_storage()

    session = SessionLocal()
    saved = []
    try:
        for file in items:
            # Determine size (read into memory for MVP)
            file.stream.seek(0, os.SEEK_END)
            size = file.stream.tell()
            file.stream.seek(0)

            if size > max_size:
                return jsonify({"error": f"File {file.filename} exceeds max size"}), 413

            ext = os.path.splitext(file.filename)[1]
            stored_name = f"{uuid.uuid4().hex}{ext}"
            key = _tenant_key(user.tenant_id, stored_name)

            try:
                data = file.read()
                mimetype = file.mimetype or "application/octet-stream"
                await storage.put_bytes(key, data, mimetype)

                # Persist: for local we store absolute path; for S3 we store the key
                local_path = await storage.local_path_for(key)
                stored_path = local_path if local_path else key
            except Exception as e:
                session.rollback()
                return jsonify(
                    {"error": f"Storage error: {type(e).__name__}", "detail": str(e)}
                ), 500

            rec = File(
                tenant_id=user.tenant_id,
                user_id=user.id,
                filename=file.filename,
                stored_name=stored_name,
                path=stored_path,
                size=size,
                mimetype=mimetype,
                uploaded_at=datetime.utcnow(),
            )
            session.add(rec)
            session.flush()
            saved.append(rec)

        session.commit()
        return jsonify([r.to_dict() for r in saved]), 201
    except SQLAlchemyError:
        session.rollback()
        return jsonify({"error": "Database error"}), 500
    finally:
        session.close()

@storage_bp.route("/download/<int:file_id>", methods=["GET"])
@requires_auth()
async def download_file(file_id: int):
    user = request.user
    session = SessionLocal()
    try:
        rec = (
            session.query(File)
            .filter(File.id == file_id, File.tenant_id == user.tenant_id)
            .first()
        )
        if not rec:
            return jsonify({"error": "File not found"}), 404

        # Backward‑compatible: if this record has a local absolute path and exists, serve it
        if os.path.isabs(rec.path) and os.path.exists(rec.path):
            return await send_file(
                rec.path,
                as_attachment=True,
                attachment_filename=rec.filename,  # keep your existing arg
                mimetype=rec.mimetype,
            )

        # Otherwise treat File.path as an object key in S3‑compatible storage
        storage = get_storage()
        try:
            data, content_type = await storage.get_bytes(rec.path)
        except Exception:
            return jsonify({"error": "File not found in storage"}), 404

        headers = {
            "Content-Type": content_type or rec.mimetype or "application/octet-stream",
            "Content-Disposition": f'attachment; filename="{rec.filename}"',
        }
        return Response(data, headers=headers)
    finally:
        session.close()

@storage_bp.route("/delete/<int:file_id>", methods=["DELETE"])
@requires_auth(roles=["file_uploads"])
async def delete_file(file_id: int):
    user = request.user
    session = SessionLocal()
    try:
        rec = (
            session.query(File)
            .filter(File.id == file_id, File.tenant_id == user.tenant_id)
            .first()
        )
        if not rec:
            return jsonify({"error": "File not found"}), 404

        # Try S3 key delete, else local file delete (backward‑compatible)
        storage = get_storage()
        if os.path.isabs(rec.path):
            try:
                if os.path.exists(rec.path):
                    os.remove(rec.path)
            except Exception:
                pass
        else:
            try:
                await storage.delete(rec.path)
            except Exception:
                pass

        session.delete(rec)
        session.commit()
        return jsonify({"message": "Deleted"})
    finally:
        session.close()
