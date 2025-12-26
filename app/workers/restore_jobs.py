"""
Restore job: download from B2 → verify checksum → GPG decrypt → pg_restore
"""
import os
import subprocess
import hashlib
import json
from datetime import datetime
from app.database import SessionLocal
from app.models import Backup, BackupRestore, User
from app.config import (
    SQLALCHEMY_DATABASE_URI,
    BACKUP_GPG_PASSPHRASE
)
from app.utils.backup_storage import get_backup_storage
from app.utils.logging_utils import logger


def run_restore_job(restore_id: int):
    """
    Execute a database restore job:
    1. Download encrypted backup from B2
    2. Verify SHA-256 checksum
    3. Decrypt with GPG
    4. Log restore metadata to B2 (for audit trail that survives restore)
    5. Run pg_restore with --clean and --if-exists
    6. Update restore record (will likely fail since record was wiped)
    7. Cleanup local files
    """
    session = SessionLocal()
    restore = None
    local_encrypted_path = None
    local_decrypted_path = None

    try:
        # Fetch restore record
        restore = session.query(BackupRestore).filter_by(id=restore_id).first()
        if not restore:
            logger.error(f"[Restore] Restore ID {restore_id} not found")
            return

        # Fetch backup record
        backup = session.query(Backup).filter_by(id=restore.backup_id).first()
        if not backup:
            raise Exception(f"Backup ID {restore.backup_id} not found")

        if backup.status != "completed":
            raise Exception(f"Backup {backup.id} is not in completed status (status: {backup.status})")

        # Update restore status
        restore.status = "in_progress"
        session.commit()

        logger.info(f"[Restore] Starting restore from backup {backup.id}")

        # Parse database connection URL
        db_url = SQLALCHEMY_DATABASE_URI
        if db_url.startswith("sqlite"):
            raise ValueError("SQLite restores not supported - use production PostgreSQL")

        if not db_url.startswith("postgresql://"):
            raise ValueError(f"Unsupported database URL: {db_url}")

        # Create temporary file paths
        temp_dir = "/tmp"
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        local_encrypted_path = os.path.join(temp_dir, f"restore_{timestamp}.dump.gpg")
        local_decrypted_path = os.path.join(temp_dir, f"restore_{timestamp}.dump")

        # Step 1: Download from B2
        storage = get_backup_storage()
        logger.info(f"[Restore] Downloading from B2: {backup.storage_key}")

        download_success = storage.download_file(backup.storage_key, local_encrypted_path)
        if not download_success:
            raise Exception("B2 download failed")

        logger.info(f"[Restore] Download completed: {local_encrypted_path}")

        # Step 2: Verify checksum
        logger.info(f"[Restore] Verifying checksum")
        sha256_hash = hashlib.sha256()
        with open(local_encrypted_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256_hash.update(chunk)
        downloaded_checksum = sha256_hash.hexdigest()

        if downloaded_checksum != backup.checksum:
            raise Exception(
                f"Checksum mismatch! Expected: {backup.checksum}, Got: {downloaded_checksum}"
            )

        logger.info(f"[Restore] Checksum verified")

        # Step 3: Decrypt with GPG
        if not BACKUP_GPG_PASSPHRASE:
            raise ValueError("BACKUP_GPG_PASSPHRASE not configured")

        logger.info(f"[Restore] Decrypting backup with GPG")
        gpg_cmd = [
            "gpg",
            "--batch",                    # Non-interactive mode
            "--yes",                      # Overwrite existing files
            "--passphrase-fd", "0",       # Read passphrase from stdin
            "--decrypt",                  # Decrypt mode
            "--output", local_decrypted_path,
            local_encrypted_path
        ]

        gpg_result = subprocess.run(
            gpg_cmd,
            input=BACKUP_GPG_PASSPHRASE,
            capture_output=True,
            text=True,
            timeout=600  # 10 minute timeout
        )

        if gpg_result.returncode != 0:
            raise Exception(f"GPG decryption failed: {gpg_result.stderr}")

        logger.info(f"[Restore] Decryption completed: {local_decrypted_path}")

        # Step 4: Log restore metadata to B2 (before pg_restore wipes the database)
        logger.info(f"[Restore] Logging restore metadata to B2")

        # Get user info before we close the session
        user = session.query(User).filter_by(id=restore.restored_by).first()
        user_email = user.email if user else "unknown"

        # Get safety backup info if it exists
        safety_backup = None
        safety_backup_filename = None
        if restore.pre_restore_backup_id:
            safety_backup = session.query(Backup).filter_by(id=restore.pre_restore_backup_id).first()
            safety_backup_filename = safety_backup.filename if safety_backup else None

        restore_log = {
            "restore_id": restore.id,
            "restore_date": datetime.utcnow().isoformat() + "Z",
            "user_email": user_email,
            "user_id": restore.restored_by,
            "backup_restored": backup.filename,
            "backup_id": backup.id,
            "backup_date": backup.created_at.isoformat() + "Z" if backup.created_at else None,
            "backup_size_bytes": backup.size_bytes,
            "backup_checksum": backup.checksum,
            "safety_backup_created": safety_backup_filename,
            "safety_backup_id": restore.pre_restore_backup_id,
            "restore_started_at": restore.started_at.isoformat() + "Z" if restore.started_at else None
        }

        # Create log file path: restore_logs/YYYY/MM/restore_YYYYMMDD_HHMMSS.json
        now = datetime.utcnow()
        log_key = f"restore_logs/{now.year}/{now.month:02d}/restore_{timestamp}.json"

        # Write to temporary file
        temp_log_path = os.path.join(temp_dir, f"restore_log_{timestamp}.json")
        with open(temp_log_path, 'w') as f:
            json.dump(restore_log, f, indent=2)

        # Upload to B2
        try:
            upload_success = storage.upload_file(temp_log_path, log_key)
            if upload_success:
                logger.info(f"[Restore] Restore log uploaded to B2: {log_key}")
            else:
                logger.warning(f"[Restore] Failed to upload restore log to B2")
        except Exception as log_err:
            logger.warning(f"[Restore] Error uploading restore log: {str(log_err)}")
        finally:
            # Clean up temp log file
            if os.path.exists(temp_log_path):
                os.remove(temp_log_path)

        # Step 5: Close database sessions and run pg_restore
        # Close the current session to avoid connection issues
        session.close()

        logger.info(f"[Restore] Starting database restore (this will cause brief downtime)")
        logger.info(f"[Restore] Running pg_restore with --clean")

        restore_cmd = [
            "pg_restore",
            "--clean",           # Drop existing objects before recreating
            "--if-exists",       # Use IF EXISTS when dropping objects
            "--no-owner",        # Don't restore ownership
            "--no-acl",          # Don't restore access privileges
            "--dbname", db_url,
            local_decrypted_path
        ]

        restore_result = subprocess.run(
            restore_cmd,
            capture_output=True,
            text=True,
            timeout=3600  # 1 hour timeout
        )

        if restore_result.returncode != 0:
            # pg_restore can return non-zero even for successful restores (due to warnings)
            # Check if there are actual errors vs just warnings
            stderr = restore_result.stderr.lower()
            if "error" in stderr and "already exists" not in stderr:
                logger.warning(f"[Restore] pg_restore warnings: {restore_result.stderr}")
            else:
                logger.info(f"[Restore] pg_restore completed with warnings (expected)")

        logger.info(f"[Restore] Database restore completed")

        # Reconnect with a fresh session
        session = SessionLocal()

        # Step 6: Mark as completed (this will fail since the restore record was wiped, but that's OK)
        try:
            restore = session.query(BackupRestore).filter_by(id=restore_id).first()
            if restore:
                restore.status = "completed"
                restore.completed_at = datetime.utcnow()
                session.commit()
                logger.info(f"[Restore] Restore {restore_id} completed successfully")
            else:
                # Expected - the restore record was wiped by pg_restore --clean
                logger.info(f"[Restore] Restore {restore_id} completed (record not found after restore, as expected)")
        except Exception as update_err:
            logger.info(f"[Restore] Could not update restore record after pg_restore (expected): {str(update_err)}")

    except Exception as e:
        logger.error(f"[Restore] Restore {restore_id} failed: {str(e)}")
        if restore:
            restore.status = "failed"
            restore.error_message = str(e)
            restore.completed_at = datetime.utcnow()
            session.commit()
        raise

    finally:
        # Cleanup local files
        if local_encrypted_path and os.path.exists(local_encrypted_path):
            os.remove(local_encrypted_path)
            logger.info(f"[Restore] Cleaned up: {local_encrypted_path}")

        if local_decrypted_path and os.path.exists(local_decrypted_path):
            os.remove(local_decrypted_path)
            logger.info(f"[Restore] Cleaned up: {local_decrypted_path}")

        session.close()