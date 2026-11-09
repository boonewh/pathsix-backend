"""Reviewed updates of leads, clients, contacts, projects and interactions.

A proposal names one accessible record and the fields to change. The review
shows each field's current and new value. Saving re-reads the record and
refuses if any changed field no longer has the value the user reviewed, so a
stale proposal cannot overwrite newer work from the CRM or another AI action.
Notes are appended unless the user asked to replace them. Reassignment uses
the CRM's admin-only assign operations; its notification is sent after commit.
Parent changes are not offered: they change who can see the record.
"""
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable

from app.models import Client, Contact, Interaction, Lead, Project, User
from app.schemas.clients import ClientAssignSchema, ClientUpdateSchema
from app.schemas.contacts import ContactUpdateSchema
from app.schemas.interactions import InteractionUpdateSchema
from app.schemas.leads import LeadAssignSchema, LeadUpdateSchema
from app.schemas.projects import ProjectAssignSchema, ProjectUpdateSchema
from app.services.access import interaction_access_filter, project_access_filter
from app.services.ai_action_kinds import ActionConflict, ActionKind
from app.services.clients import ClientService
from app.services.contacts import ContactService
from app.services.errors import RecordNotFound
from app.services.interactions import InteractionService
from app.services.leads import LeadService
from app.services.projects import ProjectService
from app.utils.lead_options import normalize_lead_options, tenant_lead_config
from app.utils.phone_utils import clean_phone_number

PHONES = ('phone', 'secondary_phone', 'primary_contact_phone')
EMAILS = ('email', 'primary_contact_email')
NOTIFICATIONS = 'ai_notifications'  # session.info key; adapters send after commit


def _plain(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    return value


@dataclass(frozen=True)
class Entity:
    noun: str
    id_key: str
    model: type
    schema: type
    fields: tuple
    get: Callable              # (session, principal, ident) -> record, enforcing access
    update: Callable           # (session, principal, ident, validated schema)
    assign: Callable = None    # (session, principal, ident, user_id) -> notification dict
    name: Callable = lambda record: record.name
    normalize: Callable = None  # (session, principal, changes) -> changes, as the service would save them


def _project(session, principal, ident):
    record = session.query(Project).filter(Project.id == ident, Project.tenant_id == principal.tenant_id,
                                           Project.deleted_at.is_(None), Project.archived_at.is_(None),
                                           project_access_filter(principal)).first()
    if record is None:
        raise RecordNotFound('Project not found')
    return record


def _interaction(session, principal, ident):
    record = session.query(Interaction).filter(Interaction.id == ident, Interaction.tenant_id == principal.tenant_id,
                                               interaction_access_filter(principal)).first()
    if record is None:
        raise RecordNotFound('Interaction not found')
    return record


def _lead_options(session, principal, changes):
    return normalize_lead_options(changes, tenant_lead_config(session, principal.tenant_id))


PARENTS = ('client_id', 'lead_id', 'project_id')

ENTITIES = {
    'lead': Entity('lead', 'lead_id', Lead, LeadUpdateSchema, tuple(LeadUpdateSchema.model_fields),
                   lambda s, p, i: LeadService(s, p)._get(i),
                   lambda s, p, i, d: LeadService(s, p).update(i, d),
                   lambda s, p, i, u: LeadService(s, p).assign(i, LeadAssignSchema(assigned_to=u)),
                   normalize=_lead_options),
    'client': Entity('client', 'client_id', Client, ClientUpdateSchema, tuple(ClientUpdateSchema.model_fields),
                     lambda s, p, i: ClientService(s, p)._get(i),
                     lambda s, p, i, d: ClientService(s, p).update(i, d),
                     lambda s, p, i, u: ClientService(s, p).assign(i, ClientAssignSchema(assigned_to=u))),
    'contact': Entity('contact', 'contact_id', Contact, ContactUpdateSchema,
                      tuple(f for f in ContactUpdateSchema.model_fields if f not in PARENTS),
                      lambda s, p, i: ContactService(s, p)._get(i),
                      lambda s, p, i, d: ContactService(s, p).update(i, d),
                      name=lambda r: ' '.join(filter(None, (r.first_name, r.last_name)))),
    'project': Entity('project', 'project_id', Project, ProjectUpdateSchema,
                      tuple(f for f in ProjectUpdateSchema.model_fields if f not in PARENTS),
                      _project, lambda s, p, i, d: ProjectService(s, p).update(i, d),
                      lambda s, p, i, u: ProjectService(s, p).assign(i, ProjectAssignSchema(assigned_to=u)),
                      name=lambda r: r.project_name),
    'interaction': Entity('interaction', 'interaction_id', Interaction, InteractionUpdateSchema,
                          tuple(f for f in InteractionUpdateSchema.model_fields if f not in PARENTS),
                          _interaction, lambda s, p, i, d: InteractionService(s, p).update(i, d),
                          name=lambda r: r.summary),
}


def _ident(value):
    if type(value) is not int or not 1 <= value <= 2147483647:
        raise ValueError('Choose one record by ID')
    return value


def _user_label(session, principal, user_id):
    if user_id is None:
        return 'Unassigned'
    user = session.query(User).filter(User.id == user_id, User.tenant_id == principal.tenant_id).first()
    return user.email if user else f'User {user_id}'


def _prepare(entity):
    def prepare(session, principal, value):
        if not isinstance(value, dict) or not {entity.id_key, 'changes'} <= set(value) \
                or set(value) - {entity.id_key, 'changes', 'notes_mode'}:
            raise ValueError(f'Give {entity.id_key} and the changes to make')
        ident = _ident(value[entity.id_key])
        mode = value.get('notes_mode', 'append')
        changes = value['changes']
        allowed = set(entity.fields) | ({'assigned_to'} if entity.assign else set())
        if mode not in ('append', 'replace') or not isinstance(changes, dict) or not changes \
                or set(changes) - allowed or any(isinstance(v, (dict, list)) for v in changes.values()):
            raise ValueError('Change only supported fields of this record')
        record = entity.get(session, principal, ident)
        fields = {key: val for key, val in changes.items() if key != 'assigned_to'}
        final, before, display = {}, {}, {}
        if fields:
            validated = entity.schema.model_validate(fields).model_dump(exclude_unset=True)
            for key, val in validated.items():
                if isinstance(val, datetime) and val.tzinfo is not None:
                    raise ActionConflict('Give dates as local times without a time zone, like 2026-11-10T10:00')
                column = entity.model.__table__.c.get(key)
                bound = getattr(getattr(column, 'type', None), 'length', None)
                if bound and isinstance(val, str) and len(val) > bound:
                    raise ValueError('A field exceeds its storage limit')
            if entity.normalize:
                validated = entity.normalize(session, principal, validated)
            for key, val in validated.items():
                if key in PHONES:
                    val = clean_phone_number(val) if val else None
                elif key in EMAILS:
                    val = str(val) if val else None
                current = _plain(getattr(record, key))
                if key == 'notes' and mode == 'append' and val:
                    display['notes_added'] = val
                    val = f'{current}\n\n{val}' if current else val
                final[key], before[key] = _plain(val), current
        if 'assigned_to' in changes:
            if not principal.is_admin:
                raise PermissionError('Only administrators can reassign records')
            user_id = _ident(changes['assigned_to'])
            if session.query(User).filter(User.id == user_id, User.tenant_id == principal.tenant_id,
                                          User.is_active.is_(True)).first() is None:
                raise RecordNotFound('Assignee not found')
            final['assigned_to'], before['assigned_to'] = user_id, record.assigned_to
            display['assigned_to'] = [_user_label(session, principal, record.assigned_to),
                                      _user_label(session, principal, user_id)]
        if final == before:
            raise ActionConflict('Nothing would change; the record already has these values')
        return {entity.id_key: ident, 'record_name': entity.name(record), 'notes_mode': mode,
                'changes': final, 'before': before, 'display': display}
    return prepare


def _execute(entity):
    def execute(session, principal, payload):
        ident = payload[entity.id_key]
        record = entity.get(session, principal, ident)
        current = {key: _plain(getattr(record, key)) for key in payload['before']}
        if current != payload['before']:
            raise ActionConflict(f'This {entity.noun} changed after you reviewed it; prepare and review a new proposal')
        fields = {key: val for key, val in payload['changes'].items() if key != 'assigned_to'}
        if fields:
            entity.update(session, principal, ident, entity.schema.model_validate(fields))
        if 'assigned_to' in payload['changes']:
            notification = entity.assign(session, principal, ident, payload['changes']['assigned_to'])
            actor = session.query(User.email).filter(User.id == principal.user_id).scalar()
            session.info.setdefault(NOTIFICATIONS, []).append(
                {key: notification[key] for key in ('to_email', 'entity_type', 'entity_name')} | {'assigned_by': actor})
        return ident
    return execute


def _describe(entity):
    def describe(payload):
        rows = [['Record', payload['record_name']]]
        for key, new in payload['changes'].items():
            label = key.replace('_', ' ').capitalize()
            if key == 'assigned_to':
                old_name, new_name = payload['display']['assigned_to']
                rows.append(['Assigned to', f'{old_name} → {new_name}'])
            elif key == 'notes' and payload['notes_mode'] == 'append' and 'notes_added' in payload['display']:
                rows.append(['Notes (adding)', payload['display']['notes_added']])
            else:
                old = payload['before'][key]
                rows.append([label + (' (replacing)' if key == 'notes' else ''),
                             f'{"(empty)" if old in (None, "") else old} → {"(empty)" if new in (None, "") else new}'])
        return rows
    return describe


def _record(entity):
    def record(session, principal, result_id):
        found = entity.get(session, principal, result_id)
        return {'id': found.id, 'name': entity.name(found)}
    return record


def update_kind(entity):
    noun = entity.noun
    return ActionKind(
        name=f'update_{noun}', scopes=(f'{noun}s:read', f'{noun}s:update'), noun=noun,
        title=f'Update this {noun}?', confirm='Save changes', done=f'{noun.capitalize()} updated', labels={},
        status_tool=f'get_{noun}_update', decide_tool=f'decide_{noun}_update',
        confirm_audit=f'confirm_{noun}_update', cancel_audit=f'cancel_{noun}_update',
        prepare=_prepare(entity), execute=_execute(entity), record=_record(entity), receipt_key=noun,
        prepare_tool=f'prepare_{noun}_update', input_key=f'{noun}_update', input_schema=entity.schema,
        describe=_describe(entity))


UPDATE_KINDS = {f'update_{noun}': update_kind(entity) for noun, entity in ENTITIES.items()}


def pending_notifications(session):
    """Assignment notifications produced by a save; send only after commit."""
    return session.info.pop(NOTIFICATIONS, [])
