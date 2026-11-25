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
from datetime import datetime

from pydantic import ValidationError
from sqlalchemy import DateTime, Enum, Float, Integer, Numeric, String, Text, select
from sqlalchemy.exc import DataError, IntegrityError

from app.models import Account, Client, Contact, Interaction, Lead, MobileRecordRef, Project, User, storage_column
from app.schemas.accounts import AccountCreateSchema, AccountUpdateSchema
from app.schemas.clients import ClientAssignSchema, ClientCreateSchema, ClientUpdateSchema
from app.schemas.contacts import ContactCreateSchema, ContactUpdateSchema
from app.schemas.interactions import InteractionCreateSchema, InteractionUpdateSchema
from app.schemas.leads import LeadAssignSchema, LeadCreateSchema, LeadUpdateSchema
from app.schemas.projects import ProjectAssignSchema, ProjectCreateSchema, ProjectUpdateSchema
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


class EditOnParent(InvalidMobileWrite):
    code = 'edit_on_parent'


class AlreadyConverted(Exception):
    code = 'already_converted'

    def __init__(self, client_uuid):
        super().__init__('This lead was already converted')
        self.client_uuid = client_uuid  # only when the caller can see that client


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
            column = storage_column(model, field)
            if not column.nullable and what != 'base':
                raise InvalidMobileWrite(f'{field} cannot be empty')
            continue
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise InvalidMobileWrite(f'{what}.{field} must be text, a number or null')
        if isinstance(value, float) and not math.isfinite(value):
            raise InvalidMobileWrite(f'{what}.{field} must be a finite number')


def _column(entity, field):
    # Person fields of clients, leads and projects are stored on their contact
    # (contacts cleanup); never read the frozen backup columns of the same names.
    return storage_column(MODELS[entity], field)


def _check_base_types(entity, base):
    """Each base value has its field's type in the feed: text for text, dates
    and enums; a number for numeric columns; or null."""
    for field, value in base.items():
        if value is None:
            continue
        kind = _column(entity, field).type
        numeric = isinstance(kind, (Integer, Float, Numeric))
        if numeric and not isinstance(value, (int, float)):
            raise InvalidMobileWrite(f'base.{field} must be a number or null')
        if isinstance(kind, (String, Text, DateTime, Enum)) and not isinstance(value, str):
            raise InvalidMobileWrite(f'base.{field} must be text or null')


def _check_parsed(entity, parsed, fields):
    """After the schema has parsed the values: finite numbers ("Infinity" as
    text becomes a float here) and text within the column's length."""
    for field in fields:
        value = getattr(parsed, field, None)
        if isinstance(value, float) and not math.isfinite(value):
            raise InvalidMobileWrite(f'{field} must be a finite number')
        length = getattr(_column(entity, field).type, 'length', None)
        if isinstance(value, str) and length and len(value) > length:
            raise InvalidMobileWrite(f'{field} must be at most {length} characters')


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
        parsed = SCHEMAS[entity][0](**fields, **{PARENT_FIELDS[k]: 1 for k in parents})
    except ValidationError as exc:
        raise InvalidMobileWrite(_validation_message(exc)) from None
    except TypeError:
        raise InvalidMobileWrite('Invalid fields') from None
    _check_parsed(entity, parsed, fields)
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
    _check_base_types(entity, base)
    try:
        parsed = SCHEMAS[entity][1](**changes)
    except ValidationError as exc:
        raise InvalidMobileWrite(_validation_message(exc)) from None
    except TypeError:  # a shared validator given the wrong type (Pydantic v2 lets it through)
        raise InvalidMobileWrite('Invalid changes') from None
    _check_parsed(entity, parsed, changes)
    return changes, base


# Step 4 (F-17): lead conversion and assignment as mobile actions.
ASSIGNABLE = {'clients': ClientAssignSchema, 'leads': LeadAssignSchema, 'projects': ProjectAssignSchema}
# The lead fields a converted client takes, read from the locked lead when the
# server applies the conversion (never from the phone's queued request).
CONVERSION_FIELDS = ('name', 'contact_person', 'contact_title', 'email', 'phone', 'phone_label',
                     'secondary_phone', 'secondary_phone_label', 'address', 'city', 'state', 'zip', 'notes')


def check_convert(body):
    """Stateless validation of a conversion: the body is {ref} only. Returns the ref."""
    if not isinstance(body, dict) or set(body) != {'ref'}:
        raise InvalidMobileWrite('Body must be {ref}')
    return _uuid(body['ref'], 'ref')


def check_assign(entity, body):
    """Stateless validation of an assignment. Returns (assigned_to, base)."""
    if entity not in ASSIGNABLE:
        raise RecordNotFound('Record not found')
    if not isinstance(body, dict) or set(body) != {'assigned_to', 'base'}:
        raise InvalidMobileWrite('Body must be {assigned_to, base}')
    assigned_to, base = body['assigned_to'], body['base']
    if type(assigned_to) is not int or assigned_to < 1:
        raise InvalidMobileWrite('assigned_to must be a user id')
    if base is not None and (type(base) is not int or base < 1):
        raise InvalidMobileWrite('base must be a user id or null')
    return assigned_to, base


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
        # FOR SHARE, rechecked under the lock: a lead being converted (FOR
        # UPDATE) is waited for, and once it is gone this create gets 404
        # instead of attaching a child to the deleted lead.
        record_id = self.session.execute(
            select(model.id).where(model.tenant_id == tenant, match, self.feed.visible(entity))
            .with_for_update(read=True, of=model)).scalar()
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
        except (IntegrityError, DataError):
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

    def _lock_visible(self, entity, record_uuid):
        model = MODELS[entity]
        row = (self.session.query(model)
               .filter(model.tenant_id == self.principal.tenant_id, model.uuid == record_uuid,
                       self.feed.visible(entity))
               .with_for_update(of=model).populate_existing().first())
        if row is None:
            raise RecordNotFound('Record not found')
        return row

    def convert(self, lead_uuid, ref):
        """Convert a lead to a client in one transaction (step 4 design, revision 4).

        The lead is locked and rechecked; a client already made from it gives
        AlreadyConverted. The client is created through ClientService with
        source_lead_id (lead type, active status, the lead's contacts with their
        ids), the lead is marked won, its interactions and projects move to the
        client, and the lead is soft-deleted. The client's ref is stored with it.
        """
        tenant = self.principal.tenant_id
        if self.session.execute(select(MobileRecordRef.id).where(
                MobileRecordRef.tenant_id == tenant, MobileRecordRef.ref == ref)).first():
            raise RefUnavailable('This ref is already in use')
        lead = self._lock_visible('leads', lead_uuid)
        existing = self.session.execute(select(Client.id, Client.uuid).where(
            Client.tenant_id == tenant, Client.source_lead_id == lead.id, Client.deleted_at.is_(None))).first()
        if existing is not None:
            visible = self.session.execute(select(Client.id).where(
                Client.id == existing.id, self.feed.visible('clients'))).first()
            raise AlreadyConverted(str(existing.uuid) if visible else None)
        # The conversion report counts leads whose status is exactly 'won' with a
        # converted_on (reports.py). Write that directly rather than through the
        # tenant option normaliser (a company option spelled 'Won' would be kept
        # as written), and keep an existing converted_on.
        lead.lead_status = 'won'
        if lead.converted_on is None:
            lead.converted_on = datetime.utcnow()
        lead.updated_at = datetime.utcnow()
        self.session.flush()
        fields = {field: getattr(lead, field) for field in CONVERSION_FIELDS}
        fields = {field: value for field, value in fields.items() if value not in (None, '')}
        try:
            data = ClientCreateSchema(**fields, source_lead_id=lead.id)
        except ValidationError as exc:
            raise InvalidMobileWrite(_validation_message(exc)) from None
        client_id = ClientService(self.session, self.principal).create(data)
        InteractionService(self.session, self.principal).transfer(lead.id, client_id)
        projects = (self.session.query(Project)
                    .filter(Project.tenant_id == tenant, Project.lead_id == lead.id)
                    .with_for_update(of=Project).all())
        for project in projects:
            project.lead_id = None
            project.client_id = client_id
        self.session.flush()
        LeadService(self.session, self.principal).delete(lead.id)
        try:
            with self.session.begin_nested():
                self.session.add(MobileRecordRef(tenant_id=tenant, ref=ref, entity='clients', record_id=client_id))
        except IntegrityError:
            raise RefUnavailable('This ref is already in use') from None
        canonical = self.session.execute(select(Client.uuid).where(Client.id == client_id)).scalar_one()
        return {'id': client_id, 'uuid': str(canonical), 'record': self.feed.record('clients', client_id)}

    def assign(self, entity, record_uuid, assigned_to, base):
        """Assign a client, lead or project (admins only), conflict-checked on assigned_to.

        Returns the usual {id, uuid, record} plus `notification` (the email to
        send once committed) when this request changed the owner; None for a
        no-op, so a repeated or already-applied assignment sends no email.
        """
        if not self.principal.is_admin:
            raise PermissionError('Only administrators can assign records')
        row = self._lock_visible(entity, record_uuid)
        before = row.assigned_to
        if before == assigned_to:
            return {'id': row.id, 'uuid': str(row.uuid), 'record': self.feed.record(entity, row.id),
                    'notification': None}
        if before != base:
            raise EditConflict([{'field': 'assigned_to', 'base': base, 'mine': assigned_to, 'theirs': before}],
                               self.feed.record(entity, row.id))
        result = self._service(entity).assign(row.id, ASSIGNABLE[entity](assigned_to=assigned_to))
        self.session.flush()
        # Only the mail arguments: ProjectService.assign also returns assigned_to.
        notification = {key: result[key] for key in ('to_email', 'entity_type', 'entity_name')}
        return {'id': row.id, 'uuid': str(row.uuid), 'record': self.feed.record(entity, row.id),
                'notification': notification}

    def assignees(self):
        """Active users of the caller's tenant, for the phone's Assign picker (admins only)."""
        if not self.principal.is_admin:
            raise PermissionError('Only administrators can assign records')
        users = (self.session.query(User.id, User.email)
                 .filter(User.tenant_id == self.principal.tenant_id, User.is_active.is_(True))
                 .order_by(User.email).all())
        return [{'id': user.id, 'email': user.email} for user in users]

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
        if entity == 'contacts' and (row.is_primary or row.project_id is not None):
            # A record's main person, or a standalone project's person, is edited
            # through that record (ContactService refuses it as a contact).
            raise EditOnParent("Edit this person on their client, lead or project")
        # Person fields are stored on a contact: lock it too, so the comparison and
        # the write see the same values even if someone edits the contact directly.
        if entity in ('clients', 'leads'):
            key = Contact.client_id if entity == 'clients' else Contact.lead_id
            (self.session.query(Contact).filter(key == row.id, Contact.is_primary.is_(True))
             .with_for_update(of=Contact).populate_existing().all())
        elif entity == 'projects' and row.primary_contact_id is not None:
            (self.session.query(Contact).filter(Contact.id == row.primary_contact_id)
             .with_for_update(of=Contact).populate_existing().all())
        before = {field: _value(getattr(row, field)) for field in changes}
        record_before = self.feed.record(entity, row.id)
        savepoint = self.session.begin_nested()
        try:
            self._service(entity).update(row.id, SCHEMAS[entity][1](**changes))
            self.session.flush()
            # Re-read everything, person fields included (they come from contacts).
            self.session.expire_all()
            after = {field: _value(getattr(row, field)) for field in changes}
        except (IntegrityError, DataError):
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
