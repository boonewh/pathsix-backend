"""Shared approval registry: every kind is complete, and pages render from it."""
import pytest
from app.models import AIToolAudit, AIWriteAction
from app.services.ai_action_kinds import KINDS, CREATE_LEAD, kind_of
from app.services.ai_connections import PUBLISHED_SCOPES
from app.services.mcp_reads import TOOL_SCOPES
from test_security_boundaries import crm
from test_mcp_reads import oauth
from test_mcp_lead_creation import writer, prepare, review, decide


@pytest.mark.parametrize('kind', list(KINDS.values()), ids=list(KINDS))
def test_every_kind_is_complete_and_consistent(kind):
    assert set(kind.scopes) <= PUBLISHED_SCOPES
    # The card calls these tools, and they require the kind's scopes.
    for tool in (kind.status_tool, kind.decide_tool):
        assert set(TOOL_SCOPES[tool]) == set(kind.scopes)
    audit_width = AIToolAudit.__table__.c.tool.type.length
    assert max(len(kind.confirm_audit), len(kind.cancel_audit)) <= audit_width
    assert kind.title.endswith('?') and kind.confirm and kind.done and (kind.labels or kind.describe)
    assert all(callable(f) for f in (kind.prepare, kind.execute, kind.record))
    assert kind_of(kind.name) is kind


def test_unknown_kind_is_refused():
    with pytest.raises(ValueError):
        kind_of('delete_everything')


def test_presentation_lists_only_filled_fields_in_label_order():
    shown = CREATE_LEAD.presentation({'notes': 'Call Monday', 'name': 'Acme', 'email': '', 'city': None})
    assert shown['fields'] == [['Company', 'Acme'], ['Notes', 'Call Monday']]
    assert shown['title'] == 'Create this lead?' and shown['confirm'] == 'Create lead'
    assert shown['receipt'] == 'lead' and shown['decide_tool'] == 'decide_lead_creation'
    assert 'fields' not in CREATE_LEAD.presentation()


def test_review_page_gets_wording_and_fields_from_the_registry(oauth):
    token, _ = writer(oauth)
    action = prepare(oauth, token, lead={'name': 'A proposed lead', 'city': 'Austin'})
    status, pending = review(oauth, action)
    assert status == 200
    assert pending['presentation']['title'] == 'Create this lead?'
    assert ['Company', 'A proposed lead'] in pending['presentation']['fields']
    assert ['City', 'Austin'] in pending['presentation']['fields']
    status, saved = decide(oauth, action, pending['intent'])
    assert status == 200 and saved['status'] == 'committed'
    status, after = review(oauth, action)
    assert after['presentation']['done'] == 'Lead created' and 'fields' not in after['presentation']
    with oauth.admin() as db:
        assert db.get(AIWriteAction, action['action_id']).kind == 'create_lead'
