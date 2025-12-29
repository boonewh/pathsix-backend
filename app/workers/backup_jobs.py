"""
Backup job: pg_dump → GPG encrypt → upload to B2 → cleanup
"""
import os
import subprocess
import hashlib
from datetime import datetime, timedelta
from app.database import SessionLocal
from app.models import Backup
from app.config import (
    SQLALCHEMY_DATABASE_URI,
    BACKUP_GPG_PASSPHRASE,
    BACKUP_RETENTION_DAYS
)
from app.utils.backup_storage import get_backup_storage
from app.utils.logging_utils import logger


def run_backup_job(backup_id: int, backup_type: str = "manual"):
    """
    Execute a database backup job:
    1. Update backup status to in_progress
    2. Run pg_dump with custom format (-Fc)
    3. Encrypt with GPG (AES256 symmetric)
    4. Calculate SHA-256 checksum
    5. Upload to B2
    6. Update backup record with metadata
    7. Cleanup local files
    """
    session = SessionLocal()
    backup = None
    local_dump_path = None
    local_encrypted_path = None

    try:
        # Fetch backup record
        backup = session.query(Backup).filter_by(id=backup_id).first()
        if not backup:
            logger.error(f"[Backup] Backup ID {backup_id} not found")
            return

        # Update status
        backup.status = "in_progress"
        backup.started_at = datetime.utcnow()
        session.commit()

        # Parse database connection URL
        db_url = SQLALCHEMY_DATABASE_URI
        if db_url.startswith("sqlite"):
            raise ValueError("SQLite backups not supported - use production PostgreSQL")

        # Extract PostgreSQL connection parameters
        # Format: postgresql://user:pass@host:port/dbname
        if not db_url.startswith("postgresql://"):
            raise ValueError(f"Unsupported database URL: {db_url}")

        # Create timestamp-based filename
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = f"backup_{backup_type}_{timestamp}.dump.gpg"
        backup.filename = filename

        # Local temporary paths
        temp_dir = "/tmp"
        local_dump_path = os.path.join(temp_dir, f"backup_{timestamp}.dump")
        local_encrypted_path = os.path.join(temp_dir, filename)

        # Step 1: Run pg_dump with custom format
        logger.info(f"[Backup] Starting pg_dump for backup {backup_id}")
        dump_cmd = [
            "pg_dump",
            "--format=custom",  # -Fc for custom format (compressed, restorable)
            "--no-owner",       # Don't include ownership commands
            "--no-acl",         # Don't include access privileges
            "--file", local_dump_path,
            db_url
        ]

        result = subprocess.run(
            dump_cmd,
            capture_output=True,
            text=True,
            timeout=3600  # 1 hour timeout
        )

        if result.returncode != 0:
            raise Exception(f"pg_dump failed: {result.stderr}")

        logger.info(f"[Backup] pg_dump completed: {local_dump_path}")

        # Get dump file size
        dump_size = os.path.getsize(local_dump_path)
        backup.database_size_bytes = dump_size

        # Step 2: Encrypt with GPG (symmetric AES256)
        if not BACKUP_GPG_PASSPHRASE:
            raise ValueError("BACKUP_GPG_PASSPHRASE not configured")

        logger.info(f"[Backup] Encrypting backup with GPG")
        gpg_cmd = [
            "gpg",
            "--batch",                    # Non-interactive mode
            "--yes",                      # Overwrite existing files
            "--passphrase-fd", "0",       # Read passphrase from stdin
            "--symmetric",                # Symmetric encryption (no public key)
            "--cipher-algo", "AES256",    # Use AES256
            "--output", local_encrypted_path,
            local_dump_path
        ]

        gpg_result = subprocess.run(
            gpg_cmd,
            input=BACKUP_GPG_PASSPHRASE,
            capture_output=True,
            text=True,
            timeout=600  # 10 minute timeout
        )

        if gpg_result.returncode != 0:
            raise Exception(f"GPG encryption failed: {gpg_result.stderr}")

        logger.info(f"[Backup] Encryption completed: {local_encrypted_path}")

        # Get encrypted file size
        encrypted_size = os.path.getsize(local_encrypted_path)
        backup.size_bytes = encrypted_size

        # Step 3: Calculate SHA-256 checksum
        logger.info(f"[Backup] Calculating checksum")
        sha256_hash = hashlib.sha256()
        with open(local_encrypted_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256_hash.update(chunk)
        checksum = sha256_hash.hexdigest()
        backup.checksum = checksum

        # Step 4: Upload to B2
        storage = get_backup_storage()

        # Storage key format: backups/prod/YYYY/MM/filename
        now = datetime.utcnow()
        storage_key = f"backups/prod/{now.year}/{now.month:02d}/{filename}"
        backup.storage_key = storage_key

        logger.info(f"[Backup] Uploading to B2: {storage_key}")
        upload_success = storage.upload_file(local_encrypted_path, storage_key)

        if not upload_success:
            raise Exception("B2 upload failed")

        logger.info(f"[Backup] Upload completed")

        # Step 5: Mark as completed with robust error handling
        try:
            backup.status = "completed"
            backup.completed_at = datetime.utcnow()
            backup.database_name = db_url.split("/")[-1] if "/" in db_url else "unknown"
            session.flush()  # Catch any SQL errors before commit
            session.commit()
            logger.info(f"[Backup] Status committed to database")

            # Verify the commit persisted
            session.refresh(backup)
            if backup.status != "completed":
                logger.error(f"[Backup] Status verification failed - expected 'completed' but got '{backup.status}'")
                raise Exception("Status update did not persist to database")

            logger.info(f"[Backup] Backup {backup_id} completed successfully (verified)")

        except Exception as commit_error:
            logger.error(f"[Backup] Failed to commit completion status: {str(commit_error)}")
            session.rollback()
            # Try one more time with a fresh update
            try:
                backup.status = "completed"
                backup.completed_at = datetime.utcnow()
                session.commit()
                logger.warning(f"[Backup] Completion status committed on retry")
            except Exception as retry_error:
                logger.error(f"[Backup] Retry commit also failed: {str(retry_error)}")
                raise Exception(f"Could not persist completion status: {commit_error}")

    except Exception as e:
        logger.error(f"[Backup] Backup {backup_id} failed: {str(e)}")

        # Ensure status is always set to failed, never left in progress
        if backup:
            try:
                backup.status = "failed"
                backup.error_message = str(e)[:500]  # Limit error message length
                backup.completed_at = datetime.utcnow()
                session.flush()
                session.commit()
                logger.info(f"[Backup] Failure status committed to database")
            except Exception as status_error:
                logger.error(f"[Backup] CRITICAL: Could not update failure status: {str(status_error)}")
                # Try one last time with rollback
                try:
                    session.rollback()
                    backup.status = "failed"
                    backup.error_message = f"Backup failed: {str(e)[:400]}"
                    backup.completed_at = datetime.utcnow()
                    session.commit()
                    logger.warning(f"[Backup] Failure status committed after rollback")
                except:
                    logger.error(f"[Backup] CRITICAL: Backup {backup_id} may be stuck in 'in_progress' state")
        raise

    finally:
        # Cleanup local files
        if local_dump_path and os.path.exists(local_dump_path):
            os.remove(local_dump_path)
            logger.info(f"[Backup] Cleaned up: {local_dump_path}")

        if local_encrypted_path and os.path.exists(local_encrypted_path):
            os.remove(local_encrypted_path)
            logger.info(f"[Backup] Cleaned up: {local_encrypted_path}")

        session.close()


def cleanup_old_backups():
    """
    Delete backups older than BACKUP_RETENTION_DAYS from both database and B2.
    """
    session = SessionLocal()
    storage = get_backup_storage()

    try:
        cutoff_date = datetime.utcnow() - timedelta(days=BACKUP_RETENTION_DAYS)

        # Find old backups
        old_backups = session.query(Backup).filter(
            Backup.created_at < cutoff_date,
            Backup.status == "completed"
        ).all()

        logger.info(f"[Cleanup] Found {len(old_backups)} backups to delete (older than {BACKUP_RETENTION_DAYS} days)")

        for backup in old_backups:
            try:
                # Delete from B2
                if backup.storage_key:
                    success = storage.delete_file(backup.storage_key)
                    if success:
                        logger.info(f"[Cleanup] Deleted from B2: {backup.storage_key}")
                    else:
                        logger.warning(f"[Cleanup] Failed to delete from B2: {backup.storage_key}")

                # Delete from database
                session.delete(backup)
                session.commit()
                logger.info(f"[Cleanup] Deleted backup record: {backup.id}")

            except Exception as e:
                logger.error(f"[Cleanup] Failed to delete backup {backup.id}: {str(e)}")
                session.rollback()
                continue

        logger.info(f"[Cleanup] Cleanup completed")

    except Exception as e:
        logger.error(f"[Cleanup] Cleanup job failed: {str(e)}")
        raise

    finally:
        session.close()