#!/usr/bin/env python
"""
Scheduled backup script for Fly.io scheduled machines.

This script creates a scheduled backup and waits for it to complete.
Exit codes:
- 0: Backup completed successfully
- 1: Backup failed

Usage:
    python scripts/run_scheduled_backup.py

Configure in fly.toml:
    [[vm]]
    memory = '512mb'
    cpu_kind = 'shared'
    cpus = 1

    [processes]
    app = "hypercorn asgi:app --bind 0.0.0.0:8000"
    worker = "python scripts/run_worker.py"

    [[services]]
    processes = ["app"]
    # ... rest of service config

    # Scheduled backup (daily at 2 AM UTC)
    [[mounts]]
    source = "backups_data"
    destination = "/data"
    processes = ["scheduled_backup"]

Configure scheduled machine using flyctl:
    flyctl machines run . \
      --app pathsix-crm-backend \
      --schedule daily \
      --region iad \
      --vm-memory 512 \
      --env DATABASE_URL=... \
      --env REDIS_URL=... \
      --env BACKUP_S3_ENDPOINT_URL=... \
      --env BACKUP_S3_ACCESS_KEY_ID=... \
      --env BACKUP_S3_SECRET_ACCESS_KEY=... \
      --env BACKUP_S3_BUCKET=... \
      --env BACKUP_GPG_PASSPHRASE=... \
      --cmd "python scripts/run_scheduled_backup.py"
"""
import sys
import os
import time

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from datetime import datetime
from app.database import SessionLocal
from app.models import Backup
from app.workers.backup_jobs import run_backup_job
from app.utils.logging_utils import logger


def main():
    """Create and execute a scheduled backup."""
    logger.info("[Scheduled] Starting scheduled backup job")

    session = SessionLocal()

    try:
        # Create backup record with temporary filename (will be updated by worker)
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        backup = Backup(
            filename=f"scheduled_backup_{timestamp}.sql.gpg",
            backup_type="scheduled",
            status="pending",
            created_by=None,  # System-created
            created_at=datetime.utcnow()
        )
        session.add(backup)
        session.commit()
        session.refresh(backup)

        logger.info(f"[Scheduled] Created backup record: {backup.id}")

        backup_id = backup.id
        session.close()  # Close session before running job

        # Run backup job synchronously (no queue needed for scheduled machines)
        # Scheduled machines run once and exit, so we can block
        run_backup_job(backup_id, backup_type="scheduled")

        # Verify completion with fresh session
        verify_session = SessionLocal()
        try:
            verified_backup = verify_session.query(Backup).filter_by(id=backup_id).first()

            if verified_backup and verified_backup.status == "completed":
                logger.info(f"[Scheduled] Backup {backup_id} completed successfully")
                logger.info(f"[Scheduled] Filename: {verified_backup.filename}")
                logger.info(f"[Scheduled] Size: {verified_backup.size_bytes} bytes")
                logger.info(f"[Scheduled] Checksum: {verified_backup.checksum}")
                return 0
            else:
                logger.error(f"[Scheduled] Backup {backup_id} failed: {verified_backup.error_message if verified_backup else 'not found'}")
                return 1
        finally:
            verify_session.close()

    except Exception as e:
        logger.error(f"[Scheduled] Scheduled backup failed: {str(e)}")
        session.close()
        return 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)