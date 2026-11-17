#!/usr/bin/env python
"""
RQ worker startup script.

Usage:
    python scripts/run_worker.py

This script starts an RQ worker that processes jobs from the 'backups' queue.
Run this as a separate process group in Fly.io or as a background service locally.
"""
import sys
import os

# Add parent directory to path so we can import app modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from rq import Worker
from app.workers import redis_conn, backup_queue
from app.workers.authorization import require_platform_worker
from app.utils.logging_utils import logger


def main():
    """Start the RQ worker for backup jobs."""
    require_platform_worker()
    logger.info("Starting RQ worker for backup queue...")

    # Create worker and start listening
    worker = Worker([backup_queue], connection=redis_conn)

    logger.info(f"Worker listening on queue: {backup_queue.name}")
    logger.info("Press Ctrl+C to stop")

    try:
        worker.work(with_scheduler=True)
    except KeyboardInterrupt:
        logger.info("Worker stopped by user")
    except Exception as e:
        logger.error(f"Worker crashed: {str(e)}")
        raise


if __name__ == "__main__":
    main()