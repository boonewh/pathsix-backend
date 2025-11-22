# app/utils/storage_backend.py
from __future__ import annotations
import os
import io
import asyncio
from typing import Optional, Tuple
from quart import current_app

try:
    import boto3
    from botocore.config import Config as BotoConfig
except ImportError:
    boto3 = None
    BotoConfig = None


class StorageBackend:
    async def put_bytes(self, key: str, data: bytes, content_type: str) -> None: ...
    async def get_bytes(self, key: str) -> Tuple[bytes, str]: ...
    async def delete(self, key: str) -> None: ...
    async def local_path_for(self, key: str) -> Optional[str]: ...


class LocalStorageBackend(StorageBackend):
    def __init__(self, root: str):
        self.root = root

    def _abs(self, key: str) -> str:
        path = os.path.join(self.root, key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    async def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        abs_path = self._abs(key)
        def _write():
            with open(abs_path, "wb") as f:
                f.write(data)
        await asyncio.to_thread(_write)

    async def get_bytes(self, key: str):
        abs_path = self._abs(key)
        def _read():
            with open(abs_path, "rb") as f:
                return f.read()
        data = await asyncio.to_thread(_read)
        # Content type is tracked in DB; return generic here
        return data, "application/octet-stream"

    async def delete(self, key: str) -> None:
        abs_path = self._abs(key)
        def _delete():
            if os.path.exists(abs_path):
                os.remove(abs_path)
        await asyncio.to_thread(_delete)

    async def local_path_for(self, key: str) -> Optional[str]:
        return self._abs(key)


class S3StorageBackend(StorageBackend):
    def __init__(self, endpoint_url: str, access_key: str, secret_key: str,
                 bucket: str, region: Optional[str] = None, force_path_style: bool = True):
        if boto3 is None:
            raise RuntimeError("boto3 is not installed. pip install boto3")
        cfg = BotoConfig(
            s3={"addressing_style": "path" if force_path_style else "auto"},
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

    async def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        await asyncio.to_thread(
            self.client.put_object,
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=content_type or "application/octet-stream",
        )

    async def get_bytes(self, key: str) -> Tuple[bytes, str]:
        obj = await asyncio.to_thread(self.client.get_object, Bucket=self.bucket, Key=key)
        data = await asyncio.to_thread(obj["Body"].read)
        ctype = obj.get("ContentType", "application/octet-stream")
        return data, ctype

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self.client.delete_object, Bucket=self.bucket, Key=key)

    async def local_path_for(self, key: str) -> Optional[str]:
        return None  # not applicable for S3


def get_storage() -> StorageBackend:
    vendor = current_app.config.get("STORAGE_VENDOR", "local").lower()
    if vendor == "s3":
        return S3StorageBackend(
            endpoint_url=current_app.config["S3_ENDPOINT_URL"],
            access_key=current_app.config["S3_ACCESS_KEY_ID"],
            secret_key=current_app.config["S3_SECRET_ACCESS_KEY"],
            bucket=current_app.config["S3_BUCKET"],
            region=current_app.config.get("S3_REGION"),
            force_path_style=current_app.config.get("S3_FORCE_PATH_STYLE", True),
        )
    # default: local
    return LocalStorageBackend(current_app.config.get("STORAGE_ROOT", "./storage"))
