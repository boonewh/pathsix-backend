"""Retention: ended connections and long-expired credentials go; live history stays."""
import sys
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from app.models import AIConnection, AIToolAudit, AIWriteAction, Lead, OAuthCredential
from test_security_boundaries import crm
from test_mcp_reads import oauth, RESOURCE

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prune_ai_records  # noqa: E402

NOW = datetime(2026, 11, 15, 12, 0)


def connection(db, *, created, active_until, revoked=None, expires=None):
    ident = str(uuid4())
    db.add(AIConnection(id=ident, tenant_id=1, user_id=1, client_id='pilot', resource=RESOURCE,
                        scopes=['leads:read'], created_at=created, expires_at=expires or active_until,
                        active_until=active_until, revoked_at=revoked))
    db.flush()
    db.add(OAuthCredential(token_hash=uuid4().hex + uuid4().hex, tenant_id=1, user_id=1, connection_id=ident,
                           kind='refresh', scopes=['leads:read'], created_at=created,
                           expires_at=min(active_until, created + timedelta(days=30))))
    db.add(AIToolAudit(id=str(uuid4()), tenant_id=1, user_id=1, connection_id=ident, tool='list_leads',
                       outcome='success', result_count=1, created_at=created))
    db.add(AIWriteAction(id=str(uuid4()), tenant_id=1, user_id=1, connection_id=ident, request_key=uuid4().hex,
                         input_hash='0' * 64, kind='create_lead', payload={}, created_at=created,
                         expires_at=created + timedelta(minutes=30)))
    db.flush()
    return ident


def rows(db, ident):
    return tuple(db.query(model).filter_by(connection_id=ident).count()
                 for model in (OAuthCredential, AIToolAudit, AIWriteAction)) + (
        db.query(AIConnection).filter_by(id=ident).count(),)


def test_retention_removes_only_long_ended_connections_and_old_credentials(oauth):
    day = timedelta(days=1)
    with oauth.admin() as db:
        leads_before = db.query(Lead).count()
        old_revoked = connection(db, created=NOW - 200 * day, active_until=NOW + 100 * day, revoked=NOW - 120 * day)
        old_idle = connection(db, created=NOW - 300 * day, active_until=NOW - 100 * day)
        recent_revoked = connection(db, created=NOW - 60 * day, active_until=NOW + 30 * day, revoked=NOW - 10 * day)
        live = connection(db, created=NOW - 200 * day, active_until=NOW + 60 * day, expires=NOW + 165 * day)
        db.commit()
        dry = prune_ai_records.prune(db.connection(), NOW, apply=False)
        assert dry == {'credentials of ended connections': 2, 'tool audits of ended connections': 2,
                       'action receipts of ended connections': 2, 'ended connections': 2,
                       'expired credentials': 3}
        assert rows(db, old_revoked) == (1, 1, 1, 1)  # a dry run deletes nothing
        applied = prune_ai_records.prune(db.connection(), NOW, apply=True)
        db.commit()
        assert applied['ended connections'] == 2 and applied['expired credentials'] == 1
        assert rows(db, old_revoked) == rows(db, old_idle) == (0, 0, 0, 0)
        # Recently ended and live connections keep their audits and receipts; only
        # their long-expired credentials are removed.
        assert rows(db, recent_revoked) == (1, 1, 1, 1)
        assert rows(db, live) == (0, 1, 1, 1)
        assert db.query(Lead).count() == leads_before  # CRM records are never touched


def test_refuses_a_different_database_or_non_fly_machine(monkeypatch):
    monkeypatch.delenv('FLY_APP_NAME', raising=False)
    with pytest.raises(SystemExit):
        prune_ai_records.require_target('postgresql://admin:x@db.internal:5432/app')
    monkeypatch.setenv('FLY_APP_NAME', 'pathsixsolutions-backend-staging')
    monkeypatch.setenv('DATABASE_URL', 'postgres://runtime:x@db.internal:5432/app')
    with pytest.raises(SystemExit):
        prune_ai_records.require_target('postgresql://admin:x@other.internal:5432/app')
    with pytest.raises(SystemExit):
        prune_ai_records.require_target('postgresql://admin:x@db.internal:5432/other_db')
    assert prune_ai_records.require_target('postgresql://admin:x@db.internal:5432/app').username == 'admin'
