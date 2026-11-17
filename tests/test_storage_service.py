"""File isolation must hold independently of HTTP filters and database RLS."""
import asyncio
import io
import json
import time
from pathlib import Path

import pytest
from authlib.jose import jwt
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.datastructures import FileStorage

from test_security_boundaries import crm
from app.models import File, Role, User
from app.services.errors import RecordNotFound
from app.services.principal import Principal
from app.services.storage import StorageService, UploadTooLarge
from app.utils import auth_utils
from app.utils.storage_backend import LocalStorageBackend, StorageBackend

WRITER = Principal(1, 1, frozenset({'file_uploads'}))
READER = Principal(3, 1, frozenset())


class MemoryStorage(StorageBackend):
    def __init__(self):
        self.objects = {}
        self.calls = []
        self.fail_put = None
        self.fail_delete = False

    async def put_bytes(self, key, data, content_type):
        self.calls.append(('put', key))
        self.objects[key] = (data, content_type)
        if self.fail_put == len([c for c in self.calls if c[0] == 'put']):
            raise OSError('secret-provider-details')

    async def get_bytes(self, key):
        self.calls.append(('get', key))
        if key not in self.objects:
            raise FileNotFoundError(key)
        return self.objects[key]

    async def delete(self, key):
        self.calls.append(('delete', key))
        if self.fail_delete:
            raise OSError('secret-provider-details')
        self.objects.pop(key, None)

    async def local_path_for(self, key):
        return None


def upload(name='hello.txt', data=b'hello'):
    return FileStorage(stream=io.BytesIO(data), filename=name, content_type='text/plain')


@pytest.fixture
def files(crm, tmp_path, monkeypatch):
    call, factory, app = crm
    storage = MemoryStorage()
    root = tmp_path / 'objects'
    app.config['STORAGE_ROOT'] = str(root)
    monkeypatch.setattr('app.routes.storage.get_storage', lambda: storage)
    with factory() as db:
        user = db.get(User, 1)
        user.roles.append(Role(name='file_uploads'))
        for tenant in (1, 2):
            key = f'tenant-{tenant}/test.txt'
            db.add(File(id=tenant, tenant_id=tenant, user_id=tenant, filename=f'file-{tenant}.txt',
                        stored_name='test.txt', path=key, size=5, mimetype='text/plain'))
            storage.objects[key] = (b'hello' if tenant == 1 else b'FOREIGN', 'text/plain')
        db.commit()
        if db.bind.dialect.name == 'postgresql':
            from sqlalchemy import text
            db.execute(text("SELECT setval(pg_get_serial_sequence('files', 'id'), 2)"))
            db.commit()
    return call, factory, app, storage, root


@pytest.mark.parametrize('restricted', [False, True])
def test_direct_and_http_file_reads_are_tenant_bound(files, restricted):
    call, factory, _, storage, root = files
    runtime = auth_utils.SessionLocal if restricted else factory
    with runtime() as db:
        service = StorageService(db, READER, storage, local_root=root)
        result = service.list_files()
        assert [r['id'] for r in result] == [1]
        assert result[0]['uploadedBy'] == 'a@example.test'
        assert asyncio.run(service.download(1)) == (b'hello', 'file-1.txt', 'text/plain')
        before = list(storage.calls)
        for file_id in (2, 999, True):
            with pytest.raises(RecordNotFound):
                asyncio.run(service.download(file_id))
        assert storage.calls == before
        assert not db.new and not db.dirty
    assert json.loads(call('GET', '/api/storage/list', user=3)[1]) == result
    assert call('GET', '/api/storage/download/1', user=3) == (200, 'hello')
    assert call('GET', '/api/storage/download/2')[0] == 404
    assert call('GET', '/api/storage/list', user=None)[0] == 401


@pytest.mark.parametrize('restricted', [False, True])
def test_writer_role_and_tenant_denials_have_no_effect(files, restricted):
    call, factory, _, storage, root = files
    runtime = auth_utils.SessionLocal if restricted else factory
    for principal in (READER, Principal(1, 1, frozenset({'admin'}))):
        with runtime() as db:
            service = StorageService(db, principal, storage, local_root=root)
            with pytest.raises(PermissionError):
                asyncio.run(service.upload([upload()]))
            with pytest.raises(PermissionError):
                asyncio.run(service.delete(1))
    with runtime() as db:
        with pytest.raises(RecordNotFound):
            asyncio.run(StorageService(db, WRITER, storage, local_root=root).delete(2))
    assert storage.calls == []
    assert call('DELETE', '/api/storage/delete/1', user=3)[0] == 403
    assert call('DELETE', '/api/storage/delete/2')[0] == 404
    with factory() as db:
        db.get(User, 1).roles = []
        db.commit()
    assert call('POST', '/api/storage/upload')[0] == 403
    assert call('DELETE', '/api/storage/delete/1')[0] == 403
    with factory() as db:
        assert db.query(File).count() == 2


@pytest.mark.parametrize('pointer', ['tenant-2/test.txt', 'tenant-1/../tenant-2/test.txt',
    'tenant-1/other.txt', '/etc/passwd', 'C:\\Windows\\win.ini', 'tenant-1\\test.txt'])
def test_forged_same_tenant_metadata_never_reaches_storage(files, pointer):
    call, factory, _, storage, root = files
    with factory() as db:
        db.get(File, 1).path = pointer
        db.commit()
    for operation in ('download', 'delete'):
        with factory() as db:
            with pytest.raises(RecordNotFound):
                asyncio.run(getattr(StorageService(db, WRITER, storage, local_root=root), operation)(1))
    assert call('GET', '/api/storage/download/1')[0] == 404
    assert call('DELETE', '/api/storage/delete/1')[0] == 404
    assert storage.calls == []
    with factory() as db:
        assert db.get(File, 1).path == pointer


@pytest.mark.parametrize('restricted', [False, True])
@pytest.mark.parametrize('local', [False, True])
def test_upload_download_delete_roundtrip(files, restricted, local):
    _, factory, _, storage, root = files
    runtime = auth_utils.SessionLocal if restricted else factory
    if local:
        storage = LocalStorageBackend(root)
    with runtime() as db:
        result = asyncio.run(StorageService(db, WRITER, storage, local_root=root).upload([
            upload('../report "résumé".txt'), upload('next.csv', b'a,b')]))
    assert [r['size'] for r in result] == [5, 3]
    assert all(r['uploadedBy'] == 'a@example.test' for r in result)
    for record in result:
        with runtime() as db:
            data, filename, mimetype = asyncio.run(StorageService(db, READER, storage, local_root=root).download(record['id']))
            assert filename == record['name'] and mimetype == 'text/plain'
        with runtime() as db:
            asyncio.run(StorageService(db, WRITER, storage, local_root=root).delete(record['id']))
    with factory() as db:
        assert db.query(File).count() == 2
    if local:
        assert list(root.rglob('*.txt')) == []
        assert list(root.rglob('*.csv')) == []
    else:
        assert set(storage.objects) == {'tenant-1/test.txt', 'tenant-2/test.txt'}


def test_batch_validation_and_upload_failure_cleanup(files, monkeypatch):
    _, factory, _, storage, root = files
    for items, error in (([upload(), upload(data=b'123456')], UploadTooLarge),
                         ([upload(), upload('bad\r\nheader')], ValueError), ([], ValueError)):
        with factory() as db:
            with pytest.raises(error):
                asyncio.run(StorageService(db, WRITER, storage, local_root=root, max_size=10).upload(items))
    assert storage.calls == []
    before = dict(storage.objects)
    storage.fail_put = 2
    with factory() as db:
        with pytest.raises(OSError):
            asyncio.run(StorageService(db, WRITER, storage, local_root=root).upload([upload(), upload()]))
    assert storage.objects == before
    storage.fail_put = None
    with factory() as db:
        monkeypatch.setattr(db, 'commit', lambda: (_ for _ in ()).throw(SQLAlchemyError('commit failed')))
        with pytest.raises(SQLAlchemyError):
            asyncio.run(StorageService(db, WRITER, storage, local_root=root).upload([upload()]))
    assert storage.objects == before
    with factory() as db:
        assert db.query(File).count() == 2


def test_delete_failures_preserve_record_and_restore_bytes(files, monkeypatch):
    call, factory, _, storage, root = files
    before = dict(storage.objects)
    storage.fail_delete = True
    status, body = call('DELETE', '/api/storage/delete/1')
    assert status == 503 and 'secret-provider-details' not in body
    storage.fail_delete = False
    with factory() as db:
        monkeypatch.setattr(db, 'commit', lambda: (_ for _ in ()).throw(SQLAlchemyError('commit failed')))
        with pytest.raises(SQLAlchemyError):
            asyncio.run(StorageService(db, WRITER, storage, local_root=root).delete(1))
    assert storage.objects == before
    with factory() as db:
        assert db.get(File, 1) is not None


def test_legacy_local_paths_and_missing_objects(files):
    call, factory, _, storage, root = files
    backend = LocalStorageBackend(root)
    for key in ('tenant-1/test.txt', '1/test.txt'):
        asyncio.run(backend.put_bytes(key, b'legacy', 'text/plain'))
        with factory() as db:
            db.get(File, 1).path = asyncio.run(backend.local_path_for(key))
            db.commit()
        assert call('GET', '/api/storage/download/1') == (200, 'legacy')
        asyncio.run(backend.delete(key))
    assert call('DELETE', '/api/storage/delete/1')[0] == 200
    assert storage.calls == []


def test_http_upload_headers_and_delete(files):
    call, _, app, storage, _ = files
    token = jwt.encode({'alg': 'HS256'}, {'sub': 1, 'exp': int(time.time())+300},
                       app.config['SECRET_KEY']).decode()
    async def run():
        response = await app.test_client().post('/api/storage/upload',
            files={'files': upload('résumé "quoted".txt')},
            headers={'Authorization': f'Bearer {token}'})
        assert response.status_code == 201
        result = await response.get_json()
        response = await app.test_client().get('/api/storage/download/'+str(result[0]['id']),
            headers={'Authorization': f'Bearer {token}'})
        assert response.status_code == 200
        assert await response.get_data() == b'hello'
        assert 'attachment;' in response.headers['Content-Disposition']
        assert response.headers['Content-Type'].startswith('text/plain')
        return result[0]['id']
    file_id = asyncio.run(run())
    assert call('DELETE', f'/api/storage/delete/{file_id}') == (200, '{"message":"Deleted"}\n')
    assert len(storage.objects) == 2


def test_local_backend_rejects_escape_and_reads_do_not_create_dirs(tmp_path):
    root = tmp_path / 'objects'
    backend = LocalStorageBackend(root)
    for key in ('../outside', '/etc/passwd', 'C:\\Windows\\x', 'tenant-1/../../x', 'tenant-1\\x'):
        for operation in (lambda: backend.get_bytes(key), lambda: backend.delete(key),
                          lambda: backend.put_bytes(key, b'bad', 'text/plain')):
            with pytest.raises(ValueError):
                asyncio.run(operation())
    with pytest.raises(FileNotFoundError):
        asyncio.run(backend.get_bytes('tenant-1/missing'))
    assert not root.exists()


def test_local_backend_rejects_cross_tenant_symlinks(tmp_path):
    root = tmp_path / 'objects'
    foreign = root / 'tenant-2'
    foreign.mkdir(parents=True)
    (foreign / 'test.txt').write_bytes(b'foreign')
    try:
        (root / 'tenant-1').symlink_to(foreign, target_is_directory=True)
    except OSError:
        pytest.skip('Host does not permit creating symlinks; covered on Linux CI')
    backend = LocalStorageBackend(root)
    for operation in (lambda: backend.get_bytes('tenant-1/test.txt'),
                      lambda: backend.delete('tenant-1/test.txt'),
                      lambda: backend.put_bytes('tenant-1/test.txt', b'bad', 'text/plain')):
        with pytest.raises(ValueError):
            asyncio.run(operation())
    assert (foreign / 'test.txt').read_bytes() == b'foreign'


def test_malformed_uploader_and_stored_name_cannot_cross_tenants(files):
    _, factory, _, storage, root = files
    with factory() as db:
        record = db.get(File, 1)
        record.user_id = 2
        db.commit()
    with factory() as db:
        service = StorageService(db, READER, storage, local_root=root)
        assert service.list_files()[0]['uploadedBy'] is None
    for name in ('../test.txt', '..', 'nested/file.txt', 'test.txt:stream', 'bad\x00name'):
        with factory() as db:
            record = db.get(File, 1)
            record.stored_name = name
            record.path = 'tenant-1/' + name
            # PostgreSQL does not store NUL in text at all.
            if '\x00' in name and db.bind.dialect.name == 'postgresql':
                continue
            db.commit()
        with factory() as db:
            with pytest.raises(RecordNotFound):
                asyncio.run(StorageService(db, READER, storage, local_root=root).download(1))
    assert storage.calls == []


def test_mutations_reject_an_existing_unit_of_work(files):
    _, factory, _, storage, root = files
    with factory() as db:
        user = db.get(User, 1)
        user.email = 'pending@example.test'
        service = StorageService(db, WRITER, storage, local_root=root)
        with pytest.raises(ValueError, match='dedicated session'):
            asyncio.run(service.upload([upload()]))
        with pytest.raises(ValueError, match='dedicated session'):
            asyncio.run(service.delete(1))
        db.rollback()
    assert storage.calls == []


def test_unknown_upload_commit_retains_bytes_for_reconciliation(files, monkeypatch, caplog):
    from sqlalchemy.exc import DBAPIError
    _, factory, _, storage, root = files
    with factory() as db:
        real_commit = db.commit
        def lost_acknowledgement():
            real_commit()
            raise DBAPIError(None, None, OSError('disconnected'), connection_invalidated=True)
        monkeypatch.setattr(db, 'commit', lost_acknowledgement)
        with pytest.raises(DBAPIError):
            asyncio.run(StorageService(db, WRITER, storage, local_root=root).upload([upload()]))
    with factory() as db:
        assert db.query(File).count() == 3
    assert len(storage.objects) == 3
    assert 'commit outcome unknown' in caplog.text


def test_failed_compensation_is_reported_without_provider_secrets(files, caplog):
    _, factory, _, storage, root = files
    storage.fail_put = 1
    storage.fail_delete = True
    with factory() as db:
        with pytest.raises(OSError):
            asyncio.run(StorageService(db, WRITER, storage, local_root=root).upload([upload()]))
    assert 'compensation failed' in caplog.text
    assert 'secret-provider-details' not in caplog.text
    with factory() as db:
        assert db.query(File).count() == 2


def test_s3_adapter_missing_object_and_stream_cleanup():
    from types import SimpleNamespace
    from app.utils.storage_backend import S3StorageBackend
    class NoSuchKey(Exception):
        pass
    backend = object.__new__(S3StorageBackend)
    backend.bucket = 'test'
    body = io.BytesIO(b's3 bytes')
    backend.client = SimpleNamespace(get_object=lambda **kwargs: {'Body': body, 'ContentType': 'text/plain'},
                                     exceptions=SimpleNamespace(NoSuchKey=NoSuchKey))
    assert asyncio.run(backend.get_bytes('tenant-1/test')) == (b's3 bytes', 'text/plain')
    assert body.closed
    def missing(**kwargs):
        raise NoSuchKey()
    backend.client.get_object = missing
    with pytest.raises(FileNotFoundError):
        asyncio.run(backend.get_bytes('tenant-1/missing'))
