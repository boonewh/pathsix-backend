"""Cursor-based change feed for offline devices (mobile Phase 0, item 4).

Contract: docs/mobile-sync-feed.md. Design notes:

- Consistency. Each round takes `base = pg_snapshot_xmin(pg_current_snapshot())`
  before its first data query. Every transaction below `base` had finished by
  then, so its rows are visible to the round's later statements; the next round
  asks for `xid >= base`, so anything still in flight is delivered then. Rows can
  repeat across rounds; clients apply changes idempotently.
- Markers. sync_change_xid / sync_access_xid are maintained by triggers
  (app/utils/sync_markers.py). Permanent deletes are read from sync_purges.
- Visibility is computed explicitly from the shared access predicates. Queries
  run on the session's Connection, outside the ORM tombstone and archive filters,
  so records that left the caller's view can be reported as removals.
- A removal names only entity and uuid. Without a device it is emitted for any
  record whose own or an ancestor's access fields changed since the cursor and
  that the caller cannot see now, so it can name a record the caller never held.
- Device mode (the HTTP route always uses it; mobile Phase 0 item 6). The cursor
  is bound to the device, its sync generation and the page sequence number it
  was issued with. device_holdings lists what the feed has sent the device;
  a removal is emitted only for a held record. One page is outstanding at a
  time: presenting that page's cursor acknowledges it (its removals' holdings
  are dropped); presenting the previous cursor again means the page was lost,
  so it is recomputed and its removals are still held and re-emitted. Any other
  cursor of the device is stale and needs a full resync. Upserts become holdings
  as soon as they are sent, since the server cannot know whether they arrived.
  A full sync starts a new generation and clears the holdings; the client must
  discard its local data when it starts one. The device row is locked first, as
  revocation does, so a revoked device's holdings cannot be recreated.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import time
from uuid import UUID

from sqlalchemy import and_, case, delete, false, or_, select, text, true

from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models import Account, Client, Contact, Device, DeviceHolding, Interaction, Lead, Project, SyncPurge
from app.services.access import interaction_access_filter, owned_record_filter, project_access_filter
from app.services.database_context import bind_principal
from app.services.principal import Principal
from app.utils.project_archive_access import visible_interaction
from app.utils.tombstones import RETENTION_DAYS

DEFAULT_LIMIT = 200
MAX_LIMIT = 500
# One day short of the tombstone and sync_purges retention, so every removal a
# cursor still needs is retained when it is accepted.
CURSOR_MAX_AGE_DAYS = RETENTION_DAYS - 1
CURSOR_VERSION = 1

ENTITIES = ('clients', 'leads', 'projects', 'accounts', 'contacts', 'interactions')
PURGES = len(ENTITIES)  # resume index of the sync_purges phase (incremental only)

FIELDS = {
    'clients': ('id', 'uuid', 'name', 'contact_person', 'contact_title', 'email', 'phone', 'phone_label',
                'secondary_phone', 'secondary_phone_label', 'address', 'city', 'state', 'zip', 'status',
                'type', 'notes', 'created_by', 'assigned_to', 'created_at', 'updated_at'),
    'leads': ('id', 'uuid', 'name', 'contact_person', 'contact_title', 'email', 'phone', 'phone_label',
              'secondary_phone', 'secondary_phone_label', 'address', 'city', 'state', 'zip', 'type',
              'notes', 'lead_status', 'lead_source', 'converted_on', 'created_by', 'assigned_to',
              'created_at', 'updated_at'),
    'projects': ('id', 'uuid', 'client_id', 'lead_id', 'project_name', 'project_description', 'type',
                 'primary_contact_name', 'primary_contact_title', 'primary_contact_email',
                 'primary_contact_phone', 'primary_contact_phone_label', 'notes', 'project_status',
                 'project_start', 'project_end', 'project_worth', 'value_type', 'created_by',
                 'assigned_to', 'created_at', 'updated_at'),
    'accounts': ('id', 'uuid', 'client_id', 'account_number', 'account_name', 'status', 'opened_on',
                 'notes', 'created_at', 'updated_at'),
    'contacts': ('id', 'uuid', 'client_id', 'lead_id', 'first_name', 'last_name', 'title', 'email',
                 'phone', 'phone_label', 'secondary_phone', 'secondary_phone_label', 'notes',
                 'created_at', 'updated_at'),
    'interactions': ('id', 'uuid', 'client_id', 'lead_id', 'project_id', 'contact_person', 'email',
                     'phone', 'contact_date', 'outcome', 'notes', 'follow_up', 'followup_status',
                     'summary', 'created_by', 'created_at', 'updated_at'),
}
MODELS = {'clients': Client, 'leads': Lead, 'projects': Project, 'accounts': Account,
          'contacts': Contact, 'interactions': Interaction}
PARENTS = {'client_id': Client, 'lead_id': Lead, 'project_id': Project}


class InvalidCursor(ValueError):
    code = 'invalid_cursor'


class InvalidLimit(ValueError):
    code = 'invalid_limit'


class ResyncRequired(Exception):
    code = 'resync_required'


class UnsupportedDatabase(Exception):
    code = 'unsupported_database'


class DeviceRevoked(Exception):
    code = 'device_revoked'


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


_B64URL = re.compile(r'[A-Za-z0-9_-]+')
_LIMIT = re.compile(r'[0-9]{1,3}')


def _unb64(value):
    if not _B64URL.fullmatch(value):
        raise ValueError('not base64url')
    data = base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))
    if _b64(data) != value:  # canonical encoding only
        raise ValueError('non-canonical base64url')
    return data


def _key(secret):
    if not isinstance(secret, str) or not secret:
        raise RuntimeError('A signing secret is required')
    # Separate the cursor key from other uses of the application secret. Changing
    # SYNC_FEED_GENERATION invalidates every cursor without signing anyone out:
    # do it before reopening the feed after any restore or reload that rewinds
    # database history (docs/mobile-sync-feed.md).
    generation = os.getenv('SYNC_FEED_GENERATION', '1')
    return hmac.new(secret.encode(), b'pathsix-sync-cursor-v1\0' + generation.encode(),
                    hashlib.sha256).digest()


def encode_cursor(payload, secret):
    body = json.dumps(payload, separators=(',', ':'), sort_keys=True).encode()
    return _b64(body) + '.' + _b64(hmac.new(_key(secret), body, hashlib.sha256).digest())


def decode_cursor(token, secret):
    if not isinstance(token, str) or token.count('.') != 1 or len(token) > 1024:
        raise InvalidCursor('Invalid cursor')
    body_part, signature_part = token.split('.')
    try:
        body, signature = _unb64(body_part), _unb64(signature_part)
    except (ValueError, TypeError):
        raise InvalidCursor('Invalid cursor') from None
    if not hmac.compare_digest(signature, hmac.new(_key(secret), body, hashlib.sha256).digest()):
        raise InvalidCursor('Invalid cursor')
    try:
        payload = json.loads(body)
    except ValueError:
        raise InvalidCursor('Invalid cursor') from None
    if not isinstance(payload, dict):
        raise InvalidCursor('Invalid cursor')
    return payload


def _int(value, *, minimum=0):
    return type(value) is int and value >= minimum


def _limit(value):
    if value is None:
        return DEFAULT_LIMIT
    if isinstance(value, str):
        if not _LIMIT.fullmatch(value):
            raise InvalidLimit('limit must be an integer from 1 to 500')
        value = int(value)
    if type(value) is not int or not 1 <= value <= MAX_LIMIT:
        raise InvalidLimit('limit must be an integer from 1 to 500')
    return value


def _value(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    if hasattr(value, 'value'):  # enums
        return value.value
    return str(value)  # UUID


def _columns(entity, tenant_id):
    """The feed's columns for an entity, with each parent's uuid."""
    model = MODELS[entity]
    columns = [getattr(model, field) for field in FIELDS[entity]]
    for field, parent in PARENTS.items():
        if field in FIELDS[entity]:
            columns.append(select(parent.uuid).where(parent.id == getattr(model, field),
                                                     parent.tenant_id == tenant_id)
                           .correlate(model).scalar_subquery().label(field[:-3] + '_uuid'))
    return columns


class SyncFeedService:
    def __init__(self, session, principal: Principal):
        if not isinstance(principal, Principal):
            raise TypeError('An authenticated principal is required')
        self.session = session
        self.principal = principal
        bind_principal(session, principal)

    # -- cursor ---------------------------------------------------------
    def _state(self, cursor, secret, now, epoch, device=None):
        p = self.principal
        if cursor is None:
            return None
        payload = decode_cursor(cursor, secret)
        if payload.get('v') != CURSOR_VERSION:
            raise ResyncRequired('Cursor version is no longer supported')
        if payload.get('t') != p.tenant_id or payload.get('u') != p.user_id:
            raise InvalidCursor('Cursor belongs to another account')
        if payload.get('d') != (str(device.id) if device is not None else None):
            raise InvalidCursor('Cursor belongs to another device')
        if device is not None and (payload.get('g') != device.sync_generation or not _int(payload.get('q'), minimum=1)):
            raise ResyncRequired('This device started a newer sync; start a full sync')
        mode, since, base, issued = payload.get('m'), payload.get('s'), payload.get('b'), payload.get('i')
        key, watermark = payload.get('k'), payload.get('w')
        if (mode not in ('full', 'inc') or not _int(base) or not _int(issued)
                or (mode == 'full' and (since is not None or watermark is not None))
                or (mode == 'inc' and (not _int(since) or since > base
                                       or not _int(watermark) or watermark > issued))
                or (key is not None and not (isinstance(key, list) and len(key) == 2
                                             and _int(key[0]) and _int(key[1])
                                             and key[0] <= (PURGES if mode == 'inc' else PURGES - 1)))
                or type(payload.get('a')) is not bool):
            raise InvalidCursor('Invalid cursor')
        if payload['a'] != p.is_admin:
            raise ResyncRequired('Account permissions changed')
        if payload.get('e') != epoch:
            # sync_purges was recreated (restore or rebuild): history restarted.
            raise ResyncRequired('Sync history was reset')
        # The removals a cursor still needs date from its lower watermark `since`,
        # captured at `watermark`, not from this round's newer base. Check it on
        # every page: a round started near the deadline must not finish after it.
        oldest = watermark if mode == 'inc' else issued
        if oldest < now - CURSOR_MAX_AGE_DAYS * 86400 or issued > now + 300:
            raise ResyncRequired('Cursor is too old')
        return {'mode': mode, 'since': since, 'base': base, 'issued': issued, 'watermark': watermark,
                'key': tuple(key) if key is not None else None, 'seq': payload.get('q')}

    def _token(self, state, secret, epoch, device=None, seq=None):
        p = self.principal
        payload = {'v': CURSOR_VERSION, 't': p.tenant_id, 'u': p.user_id, 'a': p.is_admin,
                   'e': epoch, 'm': state['mode'], 's': state['since'], 'b': state['base'],
                   'i': state['issued'], 'w': state['watermark'],
                   'k': list(state['key']) if state['key'] else None, 'd': None}
        if device is not None:
            payload.update(d=str(device.id), g=device.sync_generation, q=seq)
        return encode_cursor(payload, secret)

    def _held(self, entity, uuid_column):
        return select(DeviceHolding.device_id).where(
            DeviceHolding.device_id == self.device.id, DeviceHolding.tenant_id == self.principal.tenant_id,
            DeviceHolding.entity == entity, DeviceHolding.record_uuid == uuid_column).exists()

    # -- predicates -----------------------------------------------------
    def _visible(self, entity):
        p = self.principal
        tenant = p.tenant_id
        if entity == 'clients':
            return owned_record_filter(Client, p)
        if entity == 'leads':
            return owned_record_filter(Lead, p)
        if entity == 'projects':
            return and_(Project.tenant_id == tenant, Project.deleted_at.is_(None),
                        Project.archived_at.is_(None), project_access_filter(p))
        if entity == 'accounts':
            return and_(Account.tenant_id == tenant, Account.deleted_at.is_(None),
                        Account.client.has(owned_record_filter(Client, p)))
        if entity == 'contacts':
            return and_(Contact.tenant_id == tenant, Contact.deleted_at.is_(None), or_(
                and_(Contact.client_id.isnot(None), Contact.lead_id.is_(None),
                     Contact.client.has(owned_record_filter(Client, p))),
                and_(Contact.lead_id.isnot(None), Contact.client_id.is_(None),
                     Contact.lead.has(owned_record_filter(Lead, p)))))
        return and_(Interaction.tenant_id == tenant, Interaction.deleted_at.is_(None),
                    interaction_access_filter(p), visible_interaction(Interaction))

    def _access_changed(self, entity, since):
        tenant = self.principal.tenant_id
        client = lambda rel: rel.has(and_(Client.tenant_id == tenant, Client.sync_access_xid >= since))
        lead = lambda rel: rel.has(and_(Lead.tenant_id == tenant, Lead.sync_access_xid >= since))
        if entity == 'clients':
            return Client.sync_access_xid >= since
        if entity == 'leads':
            return Lead.sync_access_xid >= since
        if entity == 'projects':
            return or_(Project.sync_access_xid >= since, client(Project.client), lead(Project.lead))
        if entity == 'accounts':
            return or_(Account.sync_access_xid >= since, client(Account.client))
        if entity == 'contacts':
            return or_(Contact.sync_access_xid >= since, client(Contact.client), lead(Contact.lead))
        return or_(Interaction.sync_access_xid >= since, client(Interaction.client), lead(Interaction.lead),
                   Interaction.project.has(and_(Project.tenant_id == tenant, or_(
                       Project.sync_access_xid >= since, client(Project.client), lead(Project.lead)))))

    # -- reads ----------------------------------------------------------
    def visible(self, entity):
        """The read-visibility predicate the feed uses (also for mobile writes)."""
        return self._visible(entity)

    def record(self, entity, record_id):
        """One record in the feed's format, or None when the caller cannot see it."""
        model = MODELS[entity]
        stmt = select(*_columns(entity, self.principal.tenant_id)).where(
            model.tenant_id == self.principal.tenant_id, model.id == record_id, self._visible(entity))
        row = self.session.execute(stmt).mappings().first()
        return {key: _value(value) for key, value in row.items()} if row else None

    def _entity_rows(self, entity, state, after_id, limit):
        model = MODELS[entity]
        visible = self._visible(entity)
        columns = _columns(entity, self.principal.tenant_id)
        if state['mode'] == 'full':
            flag, where = case((visible, True), else_=False), visible
        else:
            since = state['since']
            changed = self._access_changed(entity, since)
            flag = case((visible, True), else_=False)
            # Device mode: an access change still delivers newly visible rows (as F-4
            # does); only removals are limited to records this device holds.
            hidden = changed if self.device is None else and_(
                changed, or_(visible, self._held(entity, model.uuid)))
            where = or_(and_(visible, model.sync_change_xid >= since), hidden)
        stmt = (select(*columns, flag.label('_visible'))
                .where(model.tenant_id == self.principal.tenant_id, model.id > after_id, where)
                .order_by(model.id).limit(limit))
        out = []
        for row in self.connection.execute(stmt).mappings():
            if row['_visible']:
                record = {key: _value(value) for key, value in row.items() if key != '_visible'}
                out.append((row['id'], {'entity': entity, 'op': 'upsert', 'uuid': record['uuid'],
                                        'record': record}))
            else:
                out.append((row['id'], {'entity': entity, 'op': 'remove', 'uuid': _value(row['uuid'])}))
        return out

    def _purge_rows(self, state, after_id, limit):
        stmt = (select(SyncPurge.id, SyncPurge.entity, SyncPurge.record_uuid)
                .where(SyncPurge.tenant_id == self.principal.tenant_id, SyncPurge.id > after_id,
                       SyncPurge.purged_xid >= state['since'], SyncPurge.entity.in_(ENTITIES),
                       # A uuid the caller can see again in the same entity (operator
                       # reinsert) is not a final removal: the entity phase upserted it.
                       ~or_(*(and_(SyncPurge.entity == entity, select(model.id).where(
                           model.tenant_id == self.principal.tenant_id,
                           model.uuid == SyncPurge.record_uuid, self._visible(entity)).exists())
                              for entity, model in MODELS.items())),
                       true() if self.device is None else or_(*(
                           and_(SyncPurge.entity == entity, self._held(entity, SyncPurge.record_uuid))
                           for entity in ENTITIES)))
                .order_by(SyncPurge.id).limit(limit))
        return [(row.id, {'entity': row.entity, 'op': 'remove', 'uuid': _value(row.record_uuid)})
                for row in self.connection.execute(stmt)]

    def _lock_device(self, device_id, user):
        from app.services.devices import credential_marker, current_account
        # Current account state in this transaction (lock order: user, then device).
        account = current_account(self, self.principal.user_id)
        device = None if account is None else self.session.query(Device).populate_existing().filter(
            Device.id == device_id, Device.tenant_id == self.principal.tenant_id,
            Device.user_id == self.principal.user_id).with_for_update().first()
        if (device is None or device.revoked_at is not None
                or device.credential_marker != credential_marker(account)):
            raise DeviceRevoked('This device has been signed out')
        return device

    def _advance(self, state):
        """Apply the presented cursor's acknowledgement; return the new page's sequence number."""
        d = self.device
        if state is None:  # full sync: new generation, nothing held
            d.sync_generation += 1
            self.session.execute(delete(DeviceHolding).where(DeviceHolding.device_id == d.id,
                                                             DeviceHolding.tenant_id == d.tenant_id))
            d.sync_acked_seq, d.sync_pending_seq, d.sync_pending_removals = 0, None, None
            return 1
        seq = state['seq']
        if d.sync_pending_seq is not None and seq == d.sync_pending_seq:
            # The device applied that page: its removals are no longer held.
            for entity, uuid in d.sync_pending_removals or []:
                self.session.execute(delete(DeviceHolding).where(
                    DeviceHolding.device_id == d.id, DeviceHolding.tenant_id == d.tenant_id,
                    DeviceHolding.entity == entity, DeviceHolding.record_uuid == UUID(uuid)))
            d.sync_acked_seq, d.sync_pending_seq, d.sync_pending_removals = seq, None, None
        elif seq != d.sync_acked_seq:
            raise ResyncRequired('This cursor was superseded; start a full sync')
        # seq == acked: the outstanding page (if any) was lost; recompute it.
        return max(d.sync_acked_seq, d.sync_pending_seq or 0) + 1

    def _record(self, changes, seq):
        d = self.device
        held = [{'device_id': d.id, 'tenant_id': d.tenant_id, 'entity': c['entity'], 'record_uuid': UUID(c['uuid'])}
                for c in changes if c['op'] == 'upsert']
        if held:
            self.session.execute(pg_insert(DeviceHolding).values(held).on_conflict_do_nothing())
        d.sync_pending_seq = seq
        d.sync_pending_removals = [[c['entity'], c['uuid']] for c in changes if c['op'] == 'remove']
        self.session.flush()

    def page(self, cursor=None, limit=None, *, secret, now=None, device_id=None, user=None):
        limit = _limit(limit)
        now = int(time.time() if now is None else now)
        if self.session.get_bind().dialect.name != 'postgresql':
            raise UnsupportedDatabase('The change feed requires PostgreSQL')
        self.connection = self.session.connection()
        # Device first, as revocation locks it: serializes with sign-out.
        self.device = None if device_id is None else self._lock_device(UUID(str(device_id)), user)
        epoch = self.connection.execute(
            text("SELECT to_regclass('sync_purges')::oid::bigint")).scalar_one()
        if epoch is None:
            raise UnsupportedDatabase('Change feed storage is not installed')
        state = self._state(cursor, secret, now, epoch, self.device)
        seq = self._advance(state) if self.device is not None else None
        if state is None or state['key'] is None:
            # A new round: take its base before any data query (see module docstring).
            base = self.connection.execute(
                text('SELECT pg_snapshot_xmin(pg_current_snapshot())::text::bigint')).scalar_one()
            state = ({'mode': 'full', 'since': None, 'watermark': None} if state is None else
                     # The new lower watermark is the finished round's base, captured at its issue time.
                     {'mode': 'inc', 'since': state['base'], 'watermark': state['issued']})
            state.update(base=base, issued=now, key=(0, 0))
        last = PURGES if state['mode'] == 'inc' else PURGES - 1
        # Collect up to limit + 1 entries across phases: the extra one only tells
        # whether anything remains, so has_more is exact.
        found = []
        index, after_id = state['key']
        while index <= last and len(found) <= limit:
            want = limit + 1 - len(found)
            rows = (self._purge_rows(state, after_id, want) if index == PURGES
                    else self._entity_rows(ENTITIES[index], state, after_id, want))
            found.extend((index, row_id, change) for row_id, change in rows)
            index, after_id = index + 1, 0
        has_more = len(found) > limit
        found = found[:limit]
        changes = [change for _, _, change in found]
        if has_more:
            # Resume after the last entry returned, in its own phase.
            next_state = dict(state, key=(found[-1][0], found[-1][1]))
        else:
            # Round complete: the next round asks for everything since this base.
            next_state = {'mode': 'inc', 'since': state['base'], 'base': state['base'],
                          'issued': state['issued'], 'watermark': state['issued'], 'key': None}
        if self.device is not None:
            self._record(changes, seq)
        return {'mode': 'full' if state['mode'] == 'full' else 'incremental', 'changes': changes,
                'cursor': self._token(next_state, secret, epoch, self.device, seq), 'has_more': has_more}
