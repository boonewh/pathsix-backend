"""The parent-link repair changes only the reviewed records, or nothing at all."""
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from app.models import Interaction, Lead, Project
from test_security_boundaries import crm
from test_mcp_reads import oauth

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import repair_parent_links  # noqa: E402


def broken_records(db):
    for table, name in (('projects', 'ck_projects_at_most_one_parent'),
                        ('interactions', 'ck_interactions_one_parent')):
        db.execute(text(f'ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}'))
    db.add(Lead(id=501, tenant_id=1, name='Converted lead', created_by=1))
    db.flush()
    db.add_all([Project(id=601, tenant_id=1, created_by=1, project_name='Both parents', project_status='pending', client_id=1, lead_id=501),
                Project(id=602, tenant_id=1, created_by=1, project_name='Lead only', project_status='pending', lead_id=501),
                Interaction(id=701, tenant_id=1, summary='No parent'),
                Interaction(id=702, tenant_id=1, client_id=1, summary='Fine')])
    db.commit()


def test_repairs_exactly_the_reviewed_records(oauth):
    with oauth.admin() as db:
        broken_records(db)
        dry = repair_parent_links.repair(db.connection(), [601], [701], apply=False)
        assert dry == {'projects': [601], 'interactions': [701],
                       'changed': {'projects lead link cleared': 0, 'interactions deleted': 0}}
        assert db.get(Project, 601).lead_id == 501  # dry run changes nothing
        done = repair_parent_links.repair(db.connection(), [601], [701], apply=True)
        db.commit()
        assert done['changed'] == {'projects lead link cleared': 1, 'interactions deleted': 1}
        db.expire_all()
        assert (db.get(Project, 601).client_id, db.get(Project, 601).lead_id) == (1, None)
        assert db.get(Project, 602).lead_id == 501 and db.get(Interaction, 701) is None
        assert db.get(Interaction, 702) is not None


def test_refuses_when_records_differ_from_the_review(oauth):
    with oauth.admin() as db:
        broken_records(db)
        with pytest.raises(SystemExit):
            repair_parent_links.repair(db.connection(), [601, 999], [701], apply=True)
        with pytest.raises(SystemExit):
            repair_parent_links.repair(db.connection(), [601], [], apply=True)
        db.rollback()
        assert db.get(Project, 601).lead_id == 501 and db.get(Interaction, 701) is not None
