"""Self-registration of public AI clients: CIMD (preferred) and DCR (fallback).

Both paths create catalog rows that may request only the published MCP scopes,
never use the inline review card and never replace an operator client. Writes
go through the crm_register_ai_client database function because the runtime
role has read-only access to the client catalog.
"""
import asyncio
import json
import os
import secrets
import time
from datetime import datetime, timedelta

from sqlalchemy import exists, text
from sqlalchemy.exc import DBAPIError

from app.models import AIClient, AIConnection
from app.services.ai_connections import PUBLISHED_SCOPES
from app.services.client_metadata import (ClientMetadataError, check_public_code_client, display_name,
                                          fetch, is_metadata_client_id, redirect_list)

METADATA_REFRESH = timedelta(hours=24)
DCR_STALE = timedelta(days=1)
DCR_PER_HOUR = 200


class RegistrationUnavailable(RuntimeError):
    pass


def enabled():
    return os.getenv('MCP_CLIENT_REGISTRATION') == '1'


def store(factory, client_id, name, redirect_uris, registration):
    scopes = sorted(PUBLISHED_SCOPES)
    with factory() as db:
        if db.get_bind().dialect.name == 'postgresql':
            try:
                db.execute(text('SELECT crm_register_ai_client(:id, :name, CAST(:redirects AS json), :kind, CAST(:scopes AS json))'),
                           {'id': client_id, 'name': name, 'redirects': json.dumps(redirect_uris),
                            'kind': registration, 'scopes': json.dumps(scopes)})
            except DBAPIError:
                raise RegistrationUnavailable() from None
        else:  # Isolated SQLite tests only; mirrors the database function.
            now = datetime.utcnow()
            row = db.get(AIClient, client_id, with_for_update=True)
            if row is not None and row.registration != registration:
                raise RegistrationUnavailable()
            if registration == 'dcr':
                db.query(AIClient).filter(AIClient.registration == 'dcr', AIClient.created_at < now - DCR_STALE,
                                          ~exists().where(AIConnection.client_id == AIClient.id)
                                          ).delete(synchronize_session=False)
                recent = db.query(AIClient).filter(AIClient.registration == 'dcr',
                                                   AIClient.created_at > now - timedelta(hours=1)).count()
                if recent >= DCR_PER_HOUR:
                    raise RegistrationUnavailable()
            if row is None:
                db.add(AIClient(id=client_id, name=name, is_active=True, oauth_enabled=True, allowed_scopes=scopes,
                                redirect_uris=redirect_uris, registration=registration, created_at=now,
                                metadata_fetched_at=now, inline_review=False))
            else:
                row.name, row.redirect_uris, row.allowed_scopes, row.metadata_fetched_at = name, redirect_uris, scopes, now
        db.commit()


async def ensure_metadata_client(factory, client_id):
    """Create or refresh a CIMD client before authorization looks it up.

    A failed refresh keeps the previously verified registration; a client that
    was never fetched simply remains unknown, so authorization fails closed.
    """
    if not enabled() or not is_metadata_client_id(client_id):
        return
    with factory() as db:
        row = db.get(AIClient, client_id)
        if row is not None and (row.registration != 'cimd' or
                                (row.metadata_fetched_at and datetime.utcnow() - row.metadata_fetched_at < METADATA_REFRESH)):
            return
    try:
        metadata = await asyncio.to_thread(fetch, client_id)
    except Exception:  # Any fetch failure (DNS, TLS, IDNA, format) leaves the client unknown.
        return
    try:
        store(factory, client_id, metadata['name'], metadata['redirect_uris'], 'cimd')
    except RegistrationUnavailable:
        return


def register_dynamic(factory, data):
    """RFC 7591 registration of a public client. Returns the response body."""
    if not isinstance(data, dict):
        raise ClientMetadataError('invalid_client_metadata')
    try:
        redirects = redirect_list(data.get('redirect_uris'))
    except ClientMetadataError:
        raise ClientMetadataError('invalid_redirect_uri') from None
    # Requested secrets/auth methods are replaced: every registration is public
    # and must use PKCE, which RFC 7591 permits the server to decide.
    check_public_code_client({key: data[key] for key in ('grant_types', 'response_types') if key in data})
    name = display_name(data.get('client_name'), 'AI app')
    client_id = 'dcr-' + secrets.token_urlsafe(24)
    store(factory, client_id, name, redirects, 'dcr')
    return {'client_id': client_id, 'client_id_issued_at': int(time.time()),
            'client_name': name, 'redirect_uris': redirects,
            'grant_types': ['authorization_code', 'refresh_token'], 'response_types': ['code'],
            'token_endpoint_auth_method': 'none', 'scope': ' '.join(sorted(PUBLISHED_SCOPES))}
