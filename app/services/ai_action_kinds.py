"""Registry of reviewed AI write actions.

Each kind declares how a proposal is validated, carried out and described, and
the words the review card and page show. The approval mechanics (signed
decisions, one save per proposal, receipts, audits) live in AIActionService and
are shared by every kind. Adding a kind also requires allowing it in the
ai_write_actions kind constraint and, for new audit tool names, in tests.
"""
from dataclasses import dataclass
from typing import Callable

from app.models import Lead
from app.schemas.leads import LeadCreateSchema
from app.services.leads import LeadService


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

    def presentation(self, payload=None):
        value = {'kind': self.name, 'noun': self.noun, 'title': self.title, 'confirm': self.confirm,
                 'done': self.done, 'receipt': self.receipt_key,
                 'status_tool': self.status_tool, 'decide_tool': self.decide_tool}
        if payload is not None:
            value['fields'] = [[label, str(payload[key])] for key, label in self.labels.items()
                               if payload.get(key) not in (None, '')]
        return value


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
    prepare=_prepare_lead, execute=_create_lead, record=_lead_record, receipt_key='lead')

KINDS = {kind.name: kind for kind in (CREATE_LEAD,)}


def kind_of(name):
    try:
        return KINDS[name]
    except KeyError:
        raise ValueError('Unsupported action') from None
