"""Creates and conditional field edits for the mobile app (roadmap step 3, F-14).

Contract: docs/mobile-writes.md ("Mobile record routes"). Design: the mobile
plan, roadmap step 3.

The AI connector shares the record schemas and services, so nothing here
changes them. This adapter validates its own extra inputs (the phone's ref,
parents by uuid or ref, the edit's base), then calls the existing schema and
service inside the caller's transaction.

Edits merge field by field (owner decision, November 20, 2026). The phone sends
only the fields the user changed (`changes`) and, for each, the value it showed
(`base`). Under a row lock the service applies the change in a savepoint; a
field conflicts when the server's value had moved from the base (`before !=
base`) and the edit would change it again (`after != before`). Any conflict
rolls the savepoint back and nothing is written: the whole edit waits for the
user's choice (owner decision). Comparing `before` and `after` as the service
stores them means equivalent inputs (a formatted phone number) never conflict.
"""
import math
import uuid as uuid_lib

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import Account, Client, Contact, Interaction, Lead, MobileRecordRef, Project
from app.schemas.accounts import AccountCreateSchema, AccountUpdateSchema
from app.schemas.clients import ClientCreateSchema, ClientUpdateSchema
from app.schemas.contacts import ContactCreateSchema, ContactUpdateSchema
from app.schemas.interactions import InteractionCreateSchema, InteractionUpdateSchema
from app.schemas.leads import LeadCreateSchema, LeadUpdateSchema
from app.schemas.projects import ProjectCreateSchema, ProjectUpdateSchema
from app.services.accounts import AccountService
from app.services.clients import ClientService
from app.services.contacts import ContactService
from app.services.database_context import bind_principal
from app.services.errors import RecordNotFound
from app.services.interactions import InteractionService
from app.services.leads import LeadService
from app.services.principal import Principal
from app.services.projects import ProjectService
from app.services.sync_feed import SyncFeedService, _value

_PHONE = ('phone', 'phone_label', 'secondary_phone', 'secondary_phone_label')
_ADDRESS = ('address', 'city', 'state', 'zip')

# Fields the phone may set. Only fields the services store as given (after
# their own cleaning) are listed; parents, assignment and fields a service may
# silently ignore (account status and opened_on) are not.
EDITABLE = {
    'clients': ('name', 'contact_person', 'contact_title', 'email', *_PHONE, *_ADDRESS,
                'notes', 'type', 'status'),
    'leads': ('name', 'contact_person', 'contact_title', 'email', *_PHONE, *_ADDRESS,
              'notes', 'type', 'lead_status', 'lead_source'),
    'projects': ('project_name', 'project_description', 'type', 'project_status', 'project_start',
                 'project_end', 'project_worth', 'value_type', 'notes', 'primary_contact_name',
                 'primary_contact_title', 'primary_contact_email', 'primary_contact_phone',
                 'primary_contact_phone_label'),
    'accounts': ('account_name', 'account_number', 'notes'),
    'contacts': ('first_name', 'last_name', 'title', 'email', *_PHONE, 'notes'),
    'interactions': ('contact_date', 'summary', 'outcome', 'notes', 'follow_up', 'contact_person',
                     'email', 'phone', 'followup_status'),
}

# Which parents a create may name, and whether one is required.
PARENT_FIELDS = {'client': 'client_id', 'lead': 'lead_id', 'project': 'project_id'}
PARENT_ENTITIES = {'client': 'clients', 'lead': 'leads', 'project': 'projects'}
PARENTS = {
    'clients': ((), False),
    'leads': ((), False),
    'projects': (('client', 'lead'), False),
    'accounts': (('client',), True),
    'contacts': (('client', 'lead'), True),
    'interactions': (('client', 'lead', 'project'), True),
}

MODELS = {'clients': Client, 'leads': Lead, 'projects': Project, 'accounts': Account,
          'contacts': Contact, 'interactions': Interaction}
SCHEMAS = {
    'clients': (ClientCreateSchema, ClientUpdateSchema),
    'leads': (LeadCreateSchema, LeadUpdateSchema),
    'projects': (ProjectCreateSchema, ProjectUpdateSchema),
    'accounts': (AccountCreateSchema, AccountUpdateSchema),
    'contacts': (ContactCreateSchema, ContactUpdateSchema),
    'interactions': (InteractionCreateSchema, InteractionUpdateSchema),
}
SERVICES = {'clients': ClientService, 'leads': LeadService, 'projects': ProjectService,
            'accounts': AccountService, 'contacts': ContactService, 'interactions': InteractionService}


class InvalidMobileWrite(ValueError):
    code = 'invalid_request'


class RefUnavailable(Exception):
    code = 'ref_unavailable'


class EditConflict(Exception):
    code = 'edit_conflict'

    def __init__(self, conflicts, record):
        super().__init__('Someone changed these fields since this copy')
        self.conflicts = conflicts
        self.record = record


def _uuid(value, what):
    if not isinstance(value, str):
        raise InvalidMobileWrite(f'{what} must be a UUID')
    try:
        parsed = uuid_lib.UUID(value)
    except ValueError:
        raise InvalidMobileWrite(f'{what} must be a UUID') from None
    if str(parsed) != value.lower():
        raise InvalidMobileWrite(f'{what} must be a UUID in canonical form')
    return parsed


def _scalars(values, what, entity):
    """Every value is text, a finite number or null; required columns are not null.

    Booleans are refused (True equals 1 in Python, so a boolean base could hide
    a change), as are objects, lists and non-finite numbers (the feed's JSON
    could not carry them).
    """
    model = MODELS[entity]
    for field, value in values.items():
        if value is None:
            column = getattr(model, field).property.columns[0]
            if not column.nullable and what != 'base':
                raise InvalidMobileWrite(f'{field} cannot be empty')
            continue
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise InvalidMobileWrite(f'{what}.{field} must be text, a number or null')
        if isinstance(value, float) and not math.isfinite(value):
            raise InvalidMobileWrite(f'{what}.{field} must be a finite number')


def _entity(entity):
    if entity not in EDITABLE:
        raise RecordNotFound('Unknown record type')
    return entity


def _validation_message(exc):
    return '; '.join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()) or 'Invalid data'


def check_create(entity, body):
    """Stateless validation, before the receipt lookup. Returns (ref, fields, parents)."""
    _entity(entity)
    if not isinstance(body, dict) or set(body) - {'ref', 'fields', 'parents'}:
        raise InvalidMobileWrite('Body must be {ref, fields, parents}')
    ref = _uuid(body.get('ref'), 'ref')
    fields = body.get('fields')
    parents = body.get('parents', {})
    if not isinstance(fields, dict) or not isinstance(parents, dict):
        raise InvalidMobileWrite('fields and parents must be objects')
    unknown = set(fields) - set(EDITABLE[entity])
    if unknown:
        raise InvalidMobileWrite(f'Fields not allowed: {", ".join(sorted(unknown))}')
    _scalars(fields, 'fields', entity)
    allowed, required = PARENTS[entity]
    if set(parents) - set(allowed):
        raise InvalidMobileWrite(f'Parents not allowed: {", ".join(sorted(set(parents) - set(allowed)))}')
    if required and not parents:
        raise InvalidMobileWrite('A parent is required')
    for kind, spec in parents.items():
        if not isinstance(spec, dict) or len(spec) != 1 or not set(spec) <= {'uuid', 'ref'}:
            raise InvalidMobileWrite(f'Parent {kind} must be {{"uuid": ...}} or {{"ref": ...}}')
        _uuid(next(iter(spec.values())), f'Parent {kind}')
    # The fields alone, with placeholder parents: the real ids come after the lookup.
    try:
        SCHEMAS[entity][0](**fields, **{PARENT_FIELDS[k]: 1 for k in parents})
    except ValidationError as exc:
        raise InvalidMobileWrite(_validation_message(exc)) from None
    except TypeError:
        raise InvalidMobileWrite('Invalid fields') from None
    return ref, fields, parents


def check_edit(entity, body):
    """Stateless validation, before the receipt lookup. Returns (changes, base)."""
    _entity(entity)
    if not isinstance(body, dict) or set(body) != {'changes', 'base'}:
        raise InvalidMobileWrite('Body must be {changes, base}')
    changes, base = body['changes'], body['base']
    if not isinstance(changes, dict) or not isinstance(base, dict) or not changes:
        raise InvalidMobileWrite('changes and base must be non-empty objects')
    # A missing key and an explicit null are different: every changed field
    # needs the value the phone showed, even when that was null.
    if set(changes) != set(base):
        raise InvalidMobileWrite('changes and base must name the same fields')
    unknown = set(changes) - set(EDITABLE[entity])
    if unknown:
        raise InvalidMobileWrite(f'Fields not allowed: {", ".join(sorted(unknown))}')
    _scalars(changes, 'changes', entity)
    _scalars(base, 'base', entity)
    try:
        SCHEMAS[entity][1](**changes)
    except ValidationError as exc:
        raise InvalidMobileWrite(_validation_message(exc)) from None
    except TypeError:  # a shared validator given the wrong type (Pydantic v2 lets it through)
        raise InvalidMobileWrite('Invalid changes') from None
    return changes, base


class MobileRecordService:
    def __init__(self, session, principal: Principal):
        if not isinstance(principal, Principal):
            raise TypeError('An authenticated principal is required')
        self.session = session
        self.principal = principal
        bind_principal(session, principal)
        self.feed = SyncFeedService(session, principal)

    def _service(self, entity):
        return SERVICES[entity](self.session, self.principal)

    def _resolve_parent(self, kind, spec):
        entity = PARENT_ENTITIES[kind]
        model = MODELS[entity]
        tenant = self.principal.tenant_id
        if 'uuid' in spec:
            match = model.uuid == uuid_lib.UUID(spec['uuid'])
        else:
            match = model.id == select(MobileRecordRef.record_id).where(
                MobileRecordRef.tenant_id == tenant, MobileRecordRef.entity == entity,
                MobileRecordRef.ref == uuid_lib.UUID(spec['ref'])).scalar_subquery()
        record_id = self.session.execute(
            select(model.id).where(model.tenant_id == tenant, match, self.feed.visible(entity))).scalar()
        if record_id is None:
            raise RecordNotFound('Related record not found')
        return record_id

    def create(self, entity, ref, fields, parents):
        tenant = self.principal.tenant_id
        if self.session.execute(select(MobileRecordRef.id).where(
                MobileRecordRef.tenant_id == tenant, MobileRecordRef.ref == ref)).first():
            raise RefUnavailable('This ref is already in use')
        ids = {PARENT_FIELDS[kind]: self._resolve_parent(kind, spec) for kind, spec in parents.items()}
        data = SCHEMAS[entity][0](**fields, **ids)
        try:
            result = self._service(entity).create(data)
        except IntegrityError:
            # A permanent data problem (for example a duplicate account number):
            # the caller rolls back; retrying the same request cannot succeed.
            raise InvalidMobileWrite('This record conflicts with an existing one (for example a duplicate number)') from None
        record_id = result['id'] if isinstance(result, dict) else result
        try:
            with self.session.begin_nested():
                self.session.add(MobileRecordRef(tenant_id=tenant, ref=ref, entity=entity, record_id=record_id))
        except IntegrityError:
            # A concurrent create took the same ref first.
            raise RefUnavailable('This ref is already in use') from None
        model = MODELS[entity]
        canonical = self.session.execute(select(model.uuid).where(model.id == record_id)).scalar_one()
        return {'id': record_id, 'uuid': str(canonical), 'record': self.feed.record(entity, record_id)}

    def edit(self, entity, record_uuid, changes, base):
        model = MODELS[entity]
        tenant = self.principal.tenant_id
        # Only a row the caller can see is locked: the feed's visibility
        # predicates (record, parent, archive and tombstone rules) are part of
        # the locking query. The service re-checks its own write rules below.
        row = (self.session.query(model)
               .filter(model.tenant_id == tenant, model.uuid == record_uuid, self.feed.visible(entity))
               .with_for_update(of=model).populate_existing().first())
        if row is None:
            raise RecordNotFound('Record not found')
        before = {field: _value(getattr(row, field)) for field in changes}
        record_before = self.feed.record(entity, row.id)
        savepoint = self.session.begin_nested()
        try:
            self._service(entity).update(row.id, SCHEMAS[entity][1](**changes))
            self.session.flush()
            self.session.refresh(row)
            after = {field: _value(getattr(row, field)) for field in changes}
        except IntegrityError:
            savepoint.rollback()
            raise InvalidMobileWrite('This change conflicts with an existing record (for example a duplicate number)') from None
        except Exception:
            savepoint.rollback()
            raise
        conflicts = [
            {'field': field, 'base': base[field], 'mine': changes[field], 'theirs': before[field]}
            for field in changes
            if before[field] != base[field] and after[field] != before[field]
        ]
        if conflicts:
            savepoint.rollback()
            raise EditConflict(conflicts, record_before)
        savepoint.commit()
        return {'id': row.id, 'uuid': str(row.uuid), 'record': self.feed.record(entity, row.id)}
