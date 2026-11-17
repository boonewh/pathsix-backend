"""Tenant files; mutations own a dedicated session's commit and compensation.

Database/object storage cannot commit atomically. Crash recovery and failed
compensation require reconciliation. Do not compose mutations with other writes.
"""
import logging
import ntpath
import os
import re
from datetime import datetime
from uuid import uuid4

from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import joinedload
from app.models import File
from app.services.base import TenantService
from app.services.errors import RecordNotFound
from app.utils.storage_backend import LocalStorageBackend

logger = logging.getLogger(__name__)


class UploadTooLarge(ValueError):
    pass


class StorageFailure(Exception):
    pass


class StorageService(TenantService):
    def __init__(self, session, principal, storage, *, local_root, max_size=20*1024*1024):
        super().__init__(session, principal)
        self.storage = storage
        self.local = LocalStorageBackend(local_root)
        self.max_size = max_size or 20*1024*1024
        if type(self.max_size) is not int or self.max_size < 1:
            raise ValueError('Invalid upload size limit')

    def list_files(self):
        return [record.to_dict() for record in self._query(File)
                .options(joinedload(File.uploader)).order_by(File.uploaded_at.desc()).all()]

    def _record(self, file_id, *, lock=False):
        if type(file_id) is not int or file_id < 1:
            raise RecordNotFound('File not found')
        query = self._query(File).filter(File.id == file_id)
        if lock:
            query = query.with_for_update()
        record = query.first()
        if record is None:
            raise RecordNotFound('File not found')
        return record

    def _target(self, record):
        # A tenant-owned row is not proof its stored pointer is safe.
        name = record.stored_name
        if (not name or name in ('.', '..') or any(c in name for c in '/\\:')
                or any(ord(c) < 32 or ord(c) == 127 for c in name)):
            raise RecordNotFound('File not found')
        key = f'tenant-{self.principal.tenant_id}/{name}'
        if record.path == key:
            return self.storage, key
        # Legacy local rows must stay in this tenant's configured directory.
        for local_key in (key, f'{self.principal.tenant_id}/{name}'):
            try:
                expected = self.local._abs(local_key)
            except ValueError:
                continue
            if os.path.normcase(os.path.abspath(record.path)) == os.path.normcase(expected):
                return self.local, local_key
        raise RecordNotFound('File not found')

    async def download(self, file_id):
        record = self._record(file_id)
        backend, key = self._target(record)
        try:
            data, content_type = await backend.get_bytes(key)
        except (OSError, ValueError) as exc:
            raise RecordNotFound('File not found in storage') from exc
        except Exception as exc:
            raise StorageFailure('Storage temporarily unavailable') from exc
        return data, record.filename, record.mimetype or content_type or 'application/octet-stream'

    def _begin_mutation(self):
        # Preserve policy: admin alone does not grant file_uploads.
        if 'file_uploads' not in self.principal.roles:
            raise PermissionError('File upload permission required')
        if self.session.in_transaction():
            raise ValueError('File mutations require a fresh dedicated session')

    async def _compensate(self, operation, file_id=None):
        try:
            await operation()
        except Exception as exc:
            logger.error('Storage compensation failed; reconciliation required', extra={
                'tenant_id': self.principal.tenant_id, 'file_id': file_id,
                'exception_type': type(exc).__name__})

    async def upload(self, files):
        self._begin_mutation()
        files = list(files)
        if not files:
            raise ValueError('No files uploaded')
        prepared = []
        total = 0
        # Validate the entire batch before writing anything.
        for upload in files:
            filename = upload.filename
            if (not isinstance(filename, str) or not filename or len(filename) > 255
                    or any(ord(c) < 32 or ord(c) == 127 for c in filename)):
                raise ValueError('Invalid filename')
            upload.stream.seek(0)
            data = upload.stream.read(self.max_size - total + 1)
            total += len(data)
            if total > self.max_size:
                raise UploadTooLarge('Uploaded files exceed max size')
            extension = ntpath.splitext(filename)[1]
            if not re.fullmatch(r'\.[A-Za-z0-9]{1,16}', extension):
                extension = ''
            name = uuid4().hex + extension
            mimetype = upload.mimetype or 'application/octet-stream'
            if len(mimetype) > 100 or any(ord(c) < 32 for c in mimetype):
                raise ValueError('Invalid content type')
            prepared.append((filename, name, data, mimetype))
        written = []
        committing = False
        try:
            records = []
            for filename, name, data, mimetype in prepared:
                key = f'tenant-{self.principal.tenant_id}/{name}'
                written.append(key)  # A failed put may still have written bytes.
                await self.storage.put_bytes(key, data, mimetype)
                path = await self.storage.local_path_for(key)
                record = File(tenant_id=self.principal.tenant_id, user_id=self.principal.user_id,
                    filename=filename, stored_name=name, path=path or key, size=len(data),
                    mimetype=mimetype, uploaded_at=datetime.utcnow())
                self.session.add(record)
                records.append(record)
            self.session.flush()
            result = [record.to_dict() for record in records]
            committing = True
            self.session.commit()
            return result
        except BaseException as exc:
            self.session.rollback()
            # A lost commit acknowledgement must not cause deletion of bytes
            # whose metadata may already have committed.
            if committing and isinstance(exc, DBAPIError) and exc.connection_invalidated:
                logger.error('Storage upload commit outcome unknown; reconciliation required',
                             extra={'tenant_id': self.principal.tenant_id})
            else:
                for key in reversed(written):
                    await self._compensate(lambda key=key: self.storage.delete(key))
            raise

    async def delete(self, file_id):
        self._begin_mutation()
        record = self._record(file_id, lock=True)
        backend, key = self._target(record)
        mimetype = record.mimetype
        data = None
        try:
            try:
                data, content_type = await backend.get_bytes(key)
            except FileNotFoundError:
                pass
            await backend.delete(key)
            self.session.delete(record)
            self.session.commit()
        except BaseException:
            self.session.rollback()
            if data is not None:
                await self._compensate(lambda: backend.put_bytes(key, data, mimetype or content_type), file_id)
            raise
