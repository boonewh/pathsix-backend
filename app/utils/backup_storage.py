import boto3
from botocore.config import Config as BotoConfig
from typing import Optional


class BackupStorageBackend:
    """Dedicated storage backend for encrypted backups (separate from user documents)."""

    def __init__(self, endpoint_url: str, access_key: str, secret_key: str,
                 bucket: str, region: Optional[str] = None):
        cfg = BotoConfig(
            s3={"addressing_style": "path"},
            signature_version="s3v4",
        )
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            config=cfg,
        )
        self.bucket = bucket

    def upload_file(self, local_path: str, object_key: str) -> bool:
        """Upload a file to B2."""
        try:
            self.client.upload_file(
                local_path,
                self.bucket,
                object_key,
                ExtraArgs={'ContentType': 'application/octet-stream'}
            )
            return True
        except Exception as e:
            print(f"[BackupStorage] Upload failed: {e}")
            return False

    def download_file(self, object_key: str, local_path: str) -> bool:
        """Download a file from B2."""
        try:
            self.client.download_file(self.bucket, object_key, local_path)
            return True
        except Exception as e:
            print(f"[BackupStorage] Download failed: {e}")
            return False

    def delete_file(self, object_key: str) -> bool:
        """Delete a file from B2."""
        try:
            self.client.delete_object(Bucket=self.bucket, Key=object_key)
            return True
        except Exception as e:
            print(f"[BackupStorage] Delete failed: {e}")
            return False

    def list_files(self, prefix: str = "") -> list:
        """List all files in B2 with optional prefix filter."""
        try:
            response = self.client.list_objects_v2(
                Bucket=self.bucket,
                Prefix=prefix
            )

            if 'Contents' not in response:
                return []

            return [obj['Key'] for obj in response['Contents']]
        except Exception as e:
            print(f"[BackupStorage] List files failed: {e}")
            return []


def get_backup_storage() -> BackupStorageBackend:
    """Factory function to create backup storage backend."""
    from app.config import (
        BACKUP_S3_ENDPOINT_URL,
        BACKUP_S3_ACCESS_KEY_ID,
        BACKUP_S3_SECRET_ACCESS_KEY,
        BACKUP_S3_BUCKET,
        BACKUP_S3_REGION
    )

    if not BACKUP_S3_ENDPOINT_URL:
        raise ValueError("BACKUP_S3_ENDPOINT_URL is not configured. Set the environment variable.")

    if not BACKUP_S3_ACCESS_KEY_ID or not BACKUP_S3_SECRET_ACCESS_KEY:
        raise ValueError("B2 credentials not configured. Set BACKUP_S3_ACCESS_KEY_ID and BACKUP_S3_SECRET_ACCESS_KEY.")

    if not BACKUP_S3_BUCKET:
        raise ValueError("BACKUP_S3_BUCKET is not configured.")

    return BackupStorageBackend(
        endpoint_url=BACKUP_S3_ENDPOINT_URL,
        access_key=BACKUP_S3_ACCESS_KEY_ID,
        secret_key=BACKUP_S3_SECRET_ACCESS_KEY,
        bucket=BACKUP_S3_BUCKET,
        region=BACKUP_S3_REGION
    )
