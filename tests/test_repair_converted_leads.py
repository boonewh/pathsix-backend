"""The converted-lead repair moves exactly the reviewed stranded records, or nothing."""
import sys
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import text
from app.models import Client, Contact, Interaction, Lead, Project
from test_security_boundaries import crm
from test_mcp_reads import oauth

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import repair_converted_leads  # noqa: E402

NOW = datetime(2026, 11, 1)
EXPECTED = {'projects': {601: 501}, 'contacts': {801: 501}, 'interactions': {701: 501}}


def stranded(db):
    db.add_all([Lead(id=501, tenant_id=1, name='Converted', created_by=1, deleted_at=NOW),
                Lead(id=502, tenant_id=1, name='Just deleted', created_by=1, deleted_at=NOW),
                Lead(id=503, tenant_id=1, name='Live', created_by=1)])
    db.flush()
    db.add(Client(id=501, tenant_id=1, created_by=1, name='Converted', source_lead_id=501, converted_on=NOW))
    db.flush()
    db.add_all([
        Project(id=601, tenant_id=1, created_by=1, project_name='Stranded', project_status='pending', lead_id=501),
        Project(id=602, tenant_id=1, created_by=1, project_name='Deleted lead', project_status='pending', lead_id=502),
        Project(id=603, tenant_id=1, created_by=1, project_name='Live lead', project_status='pending', lead_id=503),
        Contact(id=801, tenant_id=1, lead_id=501, first_name='Stranded'),
        Contact(id=802, tenant_id=1, lead_id=502, first_name='Deleted lead'),
        Interaction(id=701, tenant_id=1, lead_id=501, summary='Stranded'),
        Interaction(id=702, tenant_id=1, lead_id=502, summary='Deleted lead')])
    db.commit()


def test_moves_exactly_the_reviewed_records_to_the_client(oauth):
    with oauth.admin() as db:
        stranded(db)
        dry = repair_converted_leads.repair(db.connection(), EXPECTED, apply=False)
        assert dry == {'found': EXPECTED, 'leads': 1,
                       'changed': {'projects': 0, 'contacts': 0, 'interactions': 0}}
        assert db.get(Project, 601).lead_id == 501  # The dry run changes nothing.
        done = repair_converted_leads.repair(db.connection(), EXPECTED, apply=True)
        db.commit()
        assert done['changed'] == {'projects': 1, 'contacts': 1, 'interactions': 1}
        db.expire_all()
        for model, ident in ((Project, 601), (Contact, 801), (Interaction, 701)):
            assert (db.get(model, ident).client_id, db.get(model, ident).lead_id) == (501, None)
        # Ordinary deletions and live leads are left alone.
        assert db.get(Project, 602).lead_id == 502 and db.get(Project, 603).lead_id == 503
        assert db.get(Contact, 802).lead_id == 502 and db.get(Interaction, 702).lead_id == 502
        # Running again finds nothing.
        assert repair_converted_leads.repair(db.connection(), {}, apply=True)['found'] == {
            'projects': {}, 'contacts': {}, 'interactions': {}}


def test_unreviewed_or_ambiguous_records_stop_the_repair(oauth):
    with oauth.admin() as db:
        stranded(db)
        with pytest.raises(SystemExit, match='differs from the reviewed'):
            repair_converted_leads.repair(db.connection(), {**EXPECTED, 'projects': {}}, apply=True)
        with pytest.raises(SystemExit, match='differs from the reviewed'):  # Same record, other destination.
            repair_converted_leads.repair(db.connection(), {**EXPECTED, 'projects': {601: 1}}, apply=True)
        db.rollback()
        assert db.get(Project, 601).lead_id == 501
        db.add(Client(id=502, tenant_id=1, created_by=1, name='Second', source_lead_id=501, converted_on=NOW))
        db.commit()
        with pytest.raises(SystemExit, match='several live clients'):
            repair_converted_leads.repair(db.connection(), EXPECTED, apply=True)


def test_a_destination_that_changes_after_review_stops_the_write(oauth):
    with oauth.admin() as db:
        stranded(db)
        reviewed = repair_converted_leads.repair(db.connection(), EXPECTED, apply=False)['found']
        db.get(Client, 501).deleted_at = NOW  # The reviewed destination goes away...
        db.flush()
        with pytest.raises(SystemExit):  # ...so the plan no longer matches.
            repair_converted_leads.repair(db.connection(), reviewed, apply=True)
        db.rollback()
        # Each write also rechecks its own client.
        params = {'id': 601, 'lead': 501, 'client': 1, 'tenant': 1}
        assert db.execute(text(repair_converted_leads.MOVE['projects']), params).rowcount == 0
        db.rollback()


def test_records_on_a_lead_of_another_tenant_are_not_repairable(oauth):
    with oauth.admin() as db:
        stranded(db)
        db.add(Contact(id=803, tenant_id=2, lead_id=501, first_name='Malformed'))
        db.commit()
        with pytest.raises(SystemExit, match='another tenant'):
            repair_converted_leads.repair(db.connection(), EXPECTED, apply=False)
