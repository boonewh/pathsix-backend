import asyncio
import importlib
import time
import os
import uuid
from datetime import datetime

import pytest
from authlib.jose import jwt
from quart import Quart
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Tenant, Role, User, Client, Lead, Project, Contact, Account, Interaction
from app.routes import register_blueprints
from app.utils import auth_utils


@pytest.fixture
def crm(tmp_path, monkeypatch, request):
    url = os.getenv('PURGE_TEST_DATABASE_URL')
    schema = 'purge_test_' + uuid.uuid4().hex
    admin_engine = create_engine(url, hide_parameters=True) if url else None
    engine = None

    def cleanup():
        try:
            if engine is not None:
                engine.dispose()
            if admin_engine is not None:
                # A fresh connection avoids reusing one broken during setup.
                admin_engine.dispose()
                with admin_engine.begin() as connection:
                    connection.execute(text("SET LOCAL lock_timeout='5s'"))
                    connection.execute(text("SET LOCAL statement_timeout='15s'"))
                    connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        finally:
            if admin_engine is not None:
                admin_engine.dispose()

    # Register before CREATE SCHEMA: setup failures also receive teardown.
    request.addfinalizer(cleanup)
    if admin_engine:
        with admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(url, hide_parameters=True, connect_args={'options': f'-csearch_path={schema}'})
    else:
        engine = create_engine(f"sqlite:///{tmp_path / 'security.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    for name in ('accounts', 'activity', 'subscriptions', 'contacts', 'projects', 'interactions', 'clients', 'leads', 'auth', 'reports', 'imports', 'users', 'search'):
        monkeypatch.setattr(importlib.import_module(f'app.routes.{name}'), 'SessionLocal', factory)
    monkeypatch.setattr(auth_utils, 'SessionLocal', factory)
    with factory() as db:
        db.add_all([Tenant(id=1, name='A', slug='a'), Tenant(id=2, name='B', slug='b')])
        admin = Role(name='admin')
        db.add_all([User(id=1, tenant_id=1, email='a@example.test', password_hash='unused', roles=[admin]),
                    User(id=2, tenant_id=2, email='b@example.test', password_hash='unused', roles=[admin]),
                    User(id=3, tenant_id=1, email='ordinary@example.test', password_hash='unused')])
        db.flush()
        for tenant in (1, 2):
            db.add(Client(id=tenant, tenant_id=tenant, created_by=tenant, name=f'Private client {tenant}'))
            db.add(Lead(id=tenant, tenant_id=tenant, created_by=tenant, name=f'Private lead {tenant}'))
            db.add(Project(id=tenant, tenant_id=tenant, created_by=tenant, project_name=f'Private project {tenant}', project_status='pending'))
        db.flush()
        db.add_all([Account(id=1, tenant_id=1, client_id=1, account_number='A'),
                    Contact(id=1, tenant_id=1, client_id=1, first_name='A'),
                    Interaction(id=1, tenant_id=1, client_id=1, summary='A', follow_up=datetime(2026, 9, 10)),
                    Interaction(id=2, tenant_id=2, client_id=2, summary='B', follow_up=datetime(2026, 9, 10))])
        db.commit()
        if admin_engine:
            # Explicit fixture IDs do not advance PostgreSQL sequences (unlike
            # SQLite). Keep subsequent API-created rows clear of those IDs.
            for model in (Tenant, Role, User, Client, Lead, Project, Account, Contact, Interaction):
                table = model.__tablename__
                db.execute(text(
                    f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                    f"(SELECT MAX(id) FROM {table}))"
                ))
            db.commit()
    app = Quart(__name__)
    app.config['SECRET_KEY'] = 'test-only-signing-key'
    register_blueprints(app)

    def call(method, path, body=None, user=1, claims=None):
        payload = {'sub': user, 'exp': int(time.time()) + 300, 'roles': ['admin']}
        payload.update(claims or {})
        token = jwt.encode({'alg': 'HS256'}, payload, app.config['SECRET_KEY']).decode()
        async def run():
            response = await app.test_client().open(path, method=method, json=body,
                headers={'Authorization': f'Bearer {token}'} if user else {})
            return response.status_code, await response.get_data(as_text=True)
        return asyncio.run(run())
    return call, factory, app
