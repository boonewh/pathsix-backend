"""
Admin-only backup management API.

Endpoints:
- GET /api/admin/backups - List all backups
- POST /api/admin/backups - Trigger manual backup
- GET /api/admin/backups/:id/status - Get backup status
- POST /api/admin/backups/:id/restore - Restore from backup
- DELETE /api/admin/backups/:id - Delete a backup
"""
from quart import Blueprint, request, jsonify
from datetime import datetime
from app.models import Backup, BackupRestore
from app.database import SessionLocal
from app.utils.auth_utils import requires_auth
from app.workers.backup_jobs import run_backup_job
from app.workers.restore_jobs import run_restore_job
from app.utils.logging_utils import logger

admin_backups_bp = Blueprint("admin_backups", __name__, url_prefix="/api/admin/backups")


@admin_backups_bp.route("", methods=["GET"])
@admin_backups_bp.route("", methods=["GET"])
@admin_backups_bp.route("/", methods=["GET"])
@requires_auth(roles=["admin"])
async def list_backups():
    """List all backups with pagination."""
    session = SessionLocal()
    try:
        # Query parameters
        limit = request.args.get("limit", 50, type=int)
        offset = request.args.get("offset", 0, type=int)
        status = request.args.get("status")  # Optional filter

        query = session.query(Backup).order_by(Backup.created_at.desc())

        if status:
            query = query.filter(Backup.status == status)

        total = query.count()
        backups = query.limit(limit).offset(offset).all()

        return jsonify({
            "backups": [b.to_dict() for b in backups],
            "total": total,
            "limit": limit,
            "offset": offset
        })

    finally:
        session.close()


@admin_backups_bp.route("", methods=["POST"])
@admin_backups_bp.route("", methods=["POST"])
@admin_backups_bp.route("/", methods=["POST"])
@requires_auth(roles=["admin"])
async def create_backup():
    """
    Trigger a manual backup job.

    Note: Runs synchronously (may take 30-60 seconds).
    Timeout configured to 10 minutes in fly.toml.
    """
    user = request.user
    session = SessionLocal()

    try:
        # Create backup record with a temporary filename (will be updated by worker)
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        backup = Backup(
            filename=f"manual_backup_{timestamp}.sql.gpg",
            backup_type="manual",
            status="pending",
            created_by=user.id,
            created_at=datetime.utcnow()
        )
        session.add(backup)
        session.commit()
        session.refresh(backup)

        # Run backup job synchronously (no RQ worker needed)
        logger.info(f"[Admin] Running manual backup {backup.id} by user {user.email}")
        try:
            run_backup_job(backup.id, backup_type="manual")

            # Fetch updated backup record
            session.refresh(backup)

            return jsonify({
                "message": "Backup completed successfully",
                "backup": backup.to_dict()
            }), 201
        except Exception as backup_err:
            logger.error(f"[Admin] Manual backup failed: {str(backup_err)}")
            return jsonify({
                "error": "Backup failed",
                "details": str(backup_err)
            }), 500

    finally:
        session.close()


@admin_backups_bp.route("/<int:backup_id>/status", methods=["GET"])
@requires_auth(roles=["admin"])
async def get_backup_status(backup_id: int):
    """Get detailed status of a specific backup."""
    session = SessionLocal()

    try:
        backup = session.query(Backup).filter_by(id=backup_id).first()

        if not backup:
            return jsonify({"error": "Backup not found"}), 404

        return jsonify(backup.to_dict())

    finally:
        session.close()


@admin_backups_bp.route("/<int:backup_id>/restore", methods=["POST"])
@requires_auth(roles=["admin"])
async def restore_backup(backup_id: int):
    """
    Restore database from a backup.

    This will:
    1. Create a pre-restore safety backup (automatic)
    2. Download and verify the backup
    3. Restore the database

    DANGER: This is a destructive operation!

    Note: Runs synchronously (may take 1-3 minutes).
    Timeout configured to 10 minutes in fly.toml.
    """
    user = request.user
    session = SessionLocal()

    try:
        # Verify backup exists and is completed
        backup = session.query(Backup).filter_by(id=backup_id).first()

        if not backup:
            return jsonify({"error": "Backup not found"}), 404

        if backup.status != "completed":
            return jsonify({
                "error": f"Cannot restore from backup with status: {backup.status}"
            }), 400

        # Step 1: Create pre-restore safety backup
        logger.info(f"[Admin] Creating pre-restore safety backup before restoring {backup_id}")

        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        safety_backup = Backup(
            filename=f"pre_restore_{timestamp}.sql.gpg",
            backup_type="pre_restore",
            status="pending",
            created_by=user.id,
            created_at=datetime.utcnow()
        )
        session.add(safety_backup)
        session.commit()
        session.refresh(safety_backup)

        # Run safety backup synchronously (no RQ worker needed)
        logger.info(f"[Admin] Running pre-restore safety backup {safety_backup.id}")
        try:
            run_backup_job(safety_backup.id, backup_type="pre_restore")
        except Exception as backup_err:
            logger.error(f"[Admin] Pre-restore backup failed: {str(backup_err)}")
            return jsonify({
                "error": "Failed to create pre-restore safety backup",
                "details": str(backup_err)
            }), 500

        # Step 2: Create restore record
        restore = BackupRestore(
            backup_id=backup_id,
            restored_by=user.id,
            pre_restore_backup_id=safety_backup.id,
            status="pending",
            started_at=datetime.utcnow()
        )
        session.add(restore)
        session.commit()
        session.refresh(restore)

        # Step 3: Run restore job synchronously (no RQ worker needed)
        logger.info(f"[Admin] Running restore {restore.id} (restoring from backup {backup_id})")
        try:
            run_restore_job(restore.id)
            return jsonify({
                "message": "Restore completed successfully",
                "restore_id": restore.id,
                "pre_restore_backup_id": safety_backup.id
            }), 200
        except Exception as restore_err:
            logger.error(f"[Admin] Restore failed: {str(restore_err)}")
            return jsonify({
                "error": "Restore failed",
                "details": str(restore_err)
            }), 500

    finally:
        session.close()


@admin_backups_bp.route("/<int:backup_id>", methods=["DELETE"])
@requires_auth(roles=["admin"])
async def delete_backup(backup_id: int):
    """Delete a backup from both database and B2 storage."""
    user = request.user
    session = SessionLocal()

    try:
        backup = session.query(Backup).filter_by(id=backup_id).first()

        if not backup:
            return jsonify({"error": "Backup not found"}), 404

        # Don't allow deletion of backups that are being used
        active_restore = session.query(BackupRestore).filter(
            BackupRestore.backup_id == backup_id,
            BackupRestore.status == "in_progress"
        ).first()

        if active_restore:
            return jsonify({
                "error": "Cannot delete backup - restore in progress"
            }), 400

        # Delete from B2 storage
        if backup.storage_key:
            from app.utils.backup_storage import get_backup_storage
            storage = get_backup_storage()
            storage.delete_file(backup.storage_key)

        # Delete from database
        session.delete(backup)
        session.commit()

        logger.info(f"[Admin] Backup {backup_id} deleted by user {user.email}")

        return jsonify({"message": "Backup deleted successfully"}), 200

    finally:
        session.close()


@admin_backups_bp.route("/restores", methods=["GET"])
@requires_auth(roles=["admin"])
async def list_restores():
    """
    List all restore operations from B2 restore logs.

    Since restore operations wipe the database (including the restore records),
    we fetch restore history from JSON logs stored in B2 at:
    restore_logs/YYYY/MM/restore_YYYYMMDD_HHMMSS.json
    """
    try:
        from app.utils.backup_storage import get_backup_storage
        import json

        storage = get_backup_storage()

        # List all files in restore_logs/ prefix
        restore_log_files = storage.list_files(prefix="restore_logs/")

        if not restore_log_files:
            return jsonify({
                "restores": [],
                "total": 0
            })

        # Download and parse each restore log
        restores = []
        for log_key in restore_log_files:
            try:
                # Download log file content
                import tempfile
                with tempfile.NamedTemporaryFile(mode='w+b', delete=False, suffix='.json') as tmp:
                    tmp_path = tmp.name

                download_success = storage.download_file(log_key, tmp_path)
                if download_success:
                    with open(tmp_path, 'r') as f:
                        restore_log = json.load(f)
                        restores.append(restore_log)

                    import os
                    os.remove(tmp_path)
            except Exception as parse_err:
                logger.warning(f"Failed to parse restore log {log_key}: {str(parse_err)}")
                continue

        # Sort by restore_date descending (most recent first)
        restores.sort(key=lambda x: x.get('restore_date', ''), reverse=True)

        # Apply pagination
        limit = request.args.get("limit", 50, type=int)
        offset = request.args.get("offset", 0, type=int)

        paginated_restores = restores[offset:offset + limit]

        return jsonify({
            "restores": paginated_restores,
            "total": len(restores),
            "limit": limit,
            "offset": offset
        })

    except Exception as e:
        logger.error(f"Failed to fetch restore logs from B2: {str(e)}")
        return jsonify({
            "error": "Failed to fetch restore history",
            "details": str(e)
        }), 500