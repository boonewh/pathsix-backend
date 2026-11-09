"""Registry of reviewed AI write actions.

Each kind declares how a proposal is validated, carried out and described, and
the words the review card and page show. The approval mechanics (signed
decisions, one save per proposal, receipts, audits) live in AIActionService and
are shared by every kind. Adding a kind also requires allowing it in the
ai_write_actions kind constraint (migration) and publishing its scopes.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from app.models import Client, Contact, Interaction, Lead, Project
from app.schemas.contacts import ContactCreateSchema
from app.schemas.interactions import InteractionCreateSchema
from app.schemas.leads import LeadCreateSchema
from app.schemas.projects import ProjectCreateSchema
from app.services.access import interaction_access_filter, project_access_filter
from app.services.contacts import ContactService
from app.services.errors import RecordNotFound
from app.services.interactions import InteractionService
from app.services.leads import LeadService
from app.services.projects import ProjectService


class ActionConflict(ValueError):
    pass


@dataclass(frozen=True)
class ActionKind:
    name: str
    scopes: tuple
    # Wording for the card and review page; all user-facing, never model-authored.
    noun: str
    title: str
    confirm: str
    done: str
    labels: dict
    # Tools a trusted host's card calls for this kind.
    status_tool: str
    decide_tool: str
    confirm_audit: str
    cancel_audit: str
    # prepare(session, principal, value) -> exact payload that will be saved
    prepare: Callable
    # execute(session, principal, payload) -> new record id; raises ActionConflict if stale
    execute: Callable
    # record(session, principal, result_id) -> {'id', 'name'}, rechecking access
    record: Callable
    # Receipt key kept for existing clients ('lead' for create_lead).
    receipt_key: str
    # The model-facing tool that proposes this action, and its argument name.
    prepare_tool: str
    input_key: str
    # Pydantic schema describing the proposal fields offered to the model.
    input_schema: type
    excluded_inputs: tuple = ()

    def presentation(self, payload=None):
        value = {'kind': self.name, 'noun': self.noun, 'title': self.title, 'confirm': self.confirm,
                 'done': self.done, 'receipt': self.receipt_key,
                 'status_tool': self.status_tool, 'decide_tool': self.decide_tool}
        if payload is not None:
            value['fields'] = [[label, str(payload[key])] for key, label in self.labels.items()
                               if payload.get(key) not in (None, '')]
        return value


# Leads -----------------------------------------------------------------------

def validate_lead(value):
    if not isinstance(value, dict) or set(value) - set(LeadCreateSchema.model_fields):
        raise ValueError('Supply only supported lead fields')
    if any(v is not None and not isinstance(v, str) for v in value.values()):
        raise ValueError('Lead fields must be text')
    if any(isinstance(v, str) and len(v) > 4000 for v in value.values()):
        raise ValueError('Lead fields are too long')
    validated = LeadCreateSchema.model_validate(value)
    for key, field in validated.model_dump(mode='json').items():
        bound = getattr(Lead.__table__.c[key].type, 'length', None)
        if bound and isinstance(field, str) and len(field) > bound:
            raise ValueError('Lead field exceeds its storage limit')
    return validated


def _prepare_lead(session, principal, value):
    validated = validate_lead(value)
    # The shared service prepares exactly the values it will persist, including defaults.
    fields = LeadService(session, principal).creation_fields(validated)
    validate_lead(fields)  # Also validate tenant-configured defaults before offering confirmation.
    return fields


def _create_lead(session, principal, payload):
    service = LeadService(session, principal)
    data = validate_lead(payload)
    if service.creation_fields(data) != payload:
        raise ActionConflict('Lead defaults changed; prepare and review a new proposal')
    return service.create(data)


def _lead_record(session, principal, result_id):
    # Receipts do not preserve permission to read a subsequently hidden/deleted lead.
    lead = LeadService(session, principal)._get(result_id)
    return {'id': lead.id, 'name': lead.name}


CREATE_LEAD = ActionKind(
    name='create_lead', scopes=('leads:read', 'leads:create'), noun='lead',
    title='Create this lead?', confirm='Create lead', done='Lead created',
    labels={'name': 'Company', 'contact_person': 'Contact person', 'contact_title': 'Contact title',
            'email': 'Email', 'phone': 'Phone', 'phone_label': 'Phone label',
            'secondary_phone': 'Secondary phone', 'secondary_phone_label': 'Secondary phone label',
            'address': 'Address', 'city': 'City', 'state': 'State', 'zip': 'Postal code', 'notes': 'Notes',
            'type': 'Business type', 'lead_status': 'Lead status', 'lead_source': 'Lead source'},
    status_tool='get_lead_creation', decide_tool='decide_lead_creation',
    confirm_audit='confirm_lead_creation', cancel_audit='cancel_lead_creation',
    prepare=_prepare_lead, execute=_create_lead, record=_lead_record, receipt_key='lead',
    prepare_tool='prepare_lead_creation', input_key='lead', input_schema=LeadCreateSchema)


# Contacts, projects and interactions -------------------------------------------

def _checked(schema, model, value, excluded=()):
    """Validate proposal fields with the CRM schema and the column limits."""
    allowed = set(schema.model_fields) - set(excluded)
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError('Supply only supported fields')
    if any(isinstance(v, (dict, list)) for v in value.values()):
        raise ValueError('Fields must be single values')
    if any(isinstance(v, str) and len(v) > 4000 for v in value.values()):
        raise ValueError('Fields are too long')
    validated = schema.model_validate(value)
    for key, field in validated.model_dump().items():
        if isinstance(field, datetime) and field.tzinfo is not None:
            # CRM dates are the user's local wall-clock times (see mcp_records).
            raise ValueError('Give dates as local times without a time zone, like 2026-11-10T10:00')
        column = model.__table__.c.get(key)
        bound = getattr(getattr(column, 'type', None), 'length', None)
        if bound and isinstance(field, str) and len(field) > bound:
            raise ValueError('A field exceeds its storage limit')
    return validated


PARENTS = (('client_id', Client, Client.name, 'Client'), ('lead_id', Lead, Lead.name, 'Lead'),
           ('project_id', Project, Project.project_name, 'Project'))


def _with_parent(session, payload):
    """Name the (already access-checked) parent for the review; saving ignores it."""
    for key, model, column, label in PARENTS:
        if payload.get(key):
            payload['parent'] = f'{label}: {session.query(column).filter(model.id == payload[key]).scalar()}'
    return payload


def _guarded(schema, prepare, save):
    """Save only if preparing the stored inputs again yields the reviewed payload."""
    def execute(session, principal, payload):
        inputs = {key: value for key, value in payload.items() if key in schema.model_fields}
        if prepare(session, principal, inputs) != payload:
            raise ActionConflict('The details changed; prepare and review a new proposal')
        return save(session, principal, inputs)
    return execute


def _prepare_contact(session, principal, value):
    data = _checked(ContactCreateSchema, Contact, value)
    service = ContactService(session, principal)
    service._parent(data.client_id, data.lead_id)  # Exactly one accessible client or lead.
    return _with_parent(session, service._fields(data))


def _save_contact(session, principal, inputs):
    return ContactService(session, principal).create(_checked(ContactCreateSchema, Contact, inputs))


def _contact_record(session, principal, result_id):
    contact = ContactService(session, principal)._get(result_id)
    return {'id': contact.id, 'name': ' '.join(filter(None, (contact.first_name, contact.last_name)))}


def _prepare_project(session, principal, value):
    data = _checked(ProjectCreateSchema, Project, value)
    # A project may stand alone or belong to one accessible client or lead.
    ProjectService(session, principal)._validate_parents(data.model_dump(), ('client_id', 'lead_id'), required=False)
    return _with_parent(session, data.model_dump(mode='json'))


def _save_project(session, principal, inputs):
    return ProjectService(session, principal).create(_checked(ProjectCreateSchema, Project, inputs))['id']


def _project_record(session, principal, result_id):
    project = session.query(Project).filter(Project.id == result_id, Project.tenant_id == principal.tenant_id,
                                            Project.deleted_at.is_(None), project_access_filter(principal)).first()
    if project is None:
        raise RecordNotFound('Project not found')
    return {'id': project.id, 'name': project.project_name}


INTERACTION_EXCLUDED = ('followup_status',)  # New interactions start as pending follow-ups.


def _prepare_interaction(session, principal, value):
    data = _checked(InteractionCreateSchema, Interaction, value, INTERACTION_EXCLUDED)
    InteractionService(session, principal)._validate_parents(data.model_dump(), ('client_id', 'lead_id', 'project_id'))
    return _with_parent(session, data.model_dump(mode='json', exclude={'followup_status'}))


def _save_interaction(session, principal, inputs):
    data = _checked(InteractionCreateSchema, Interaction, inputs, INTERACTION_EXCLUDED)
    return InteractionService(session, principal).create(data)['id']


def _interaction_record(session, principal, result_id):
    row = session.query(Interaction).filter(Interaction.id == result_id, Interaction.tenant_id == principal.tenant_id,
                                            interaction_access_filter(principal)).first()
    if row is None:
        raise RecordNotFound('Interaction not found')
    return {'id': row.id, 'name': row.summary}


def _record_kind(name, noun, title, confirm, done, labels, scopes, prepare, save, record, schema, excluded=()):
    return ActionKind(
        name=name, scopes=scopes, noun=noun, title=title, confirm=confirm, done=done, labels=labels,
        status_tool=f'get_{noun}_creation', decide_tool=f'decide_{noun}_creation',
        confirm_audit=f'confirm_{noun}_creation', cancel_audit=f'cancel_{noun}_creation',
        prepare=prepare, execute=_guarded(schema, prepare, save), record=record, receipt_key=noun,
        prepare_tool=f'prepare_{noun}_creation', input_key=noun, input_schema=schema, excluded_inputs=excluded)


CREATE_CONTACT = _record_kind(
    'create_contact', 'contact', 'Add this contact?', 'Add contact', 'Contact added',
    {'parent': 'For', 'first_name': 'First name', 'last_name': 'Last name', 'title': 'Title', 'email': 'Email',
     'phone': 'Phone', 'phone_label': 'Phone label', 'secondary_phone': 'Secondary phone',
     'secondary_phone_label': 'Secondary phone label', 'notes': 'Notes'},
    ('contacts:read', 'contacts:create'), _prepare_contact, _save_contact, _contact_record, ContactCreateSchema)

CREATE_PROJECT = _record_kind(
    'create_project', 'project', 'Create this project?', 'Create project', 'Project created',
    {'parent': 'For', 'project_name': 'Project', 'project_status': 'Status', 'type': 'Type',
     'project_description': 'Description', 'project_start': 'Start', 'project_end': 'End',
     'project_worth': 'Value', 'value_type': 'Value type', 'primary_contact_name': 'Primary contact',
     'primary_contact_title': 'Contact title', 'primary_contact_email': 'Contact email',
     'primary_contact_phone': 'Contact phone', 'notes': 'Notes'},
    ('projects:read', 'projects:create'), _prepare_project, _save_project, _project_record, ProjectCreateSchema)

CREATE_INTERACTION = _record_kind(
    'create_interaction', 'interaction', 'Log this interaction?', 'Log interaction', 'Interaction logged',
    {'parent': 'For', 'summary': 'Summary', 'contact_date': 'When', 'contact_person': 'Contact',
     'outcome': 'Outcome', 'follow_up': 'Follow up', 'email': 'Email', 'phone': 'Phone', 'notes': 'Notes'},
    ('interactions:read', 'interactions:create'), _prepare_interaction, _save_interaction,
    _interaction_record, InteractionCreateSchema, INTERACTION_EXCLUDED)

KINDS = {kind.name: kind for kind in (CREATE_LEAD, CREATE_CONTACT, CREATE_PROJECT, CREATE_INTERACTION)}


def kind_of(name):
    try:
        return KINDS[name]
    except KeyError:
        raise ValueError('Unsupported action') from None
