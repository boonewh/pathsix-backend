"""Refresh screenshot fixtures for the existing Staging Demo Co tenant.

On the staging machine: /venv/bin/python scripts/seed_staging.py --apply
Omit --apply to verify the fixtures without writing. The script uses the
restricted staging runtime login and leaves the existing tenant config alone.
"""
import argparse
import os
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

from sqlalchemy import or_, text
from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

APP = "pathsixsolutions-backend-staging"
DATABASE = "pathsixsolutions_backend_staging"
RUNTIME_ROLE = "pathsix_crm_staging_runtime"
DB_HOSTS = {"pathsixsolutions-db-staging.flycast", "pathsixsolutions-db-staging.internal"}
ADMIN_EMAIL = "admin@staging.test"
TENANT_SLUG = "staging"
DEMO_TIMEZONE = ZoneInfo("America/Chicago")

# The original synthetic companies can stay. Match old trials by name; the
# ordinary-user SessionLoom trial has a verified receipt for staging lead 17.
TRIAL_NAMES = (
    "MVP-LEAD-20261024-INLINE", "MVP-LEAD-20261024-CONFIRM",
    "MVP-LEAD-20261026-PHONE-CREATE", "MVP-LEAD-20261026-REFRESHED-CREATE",
    "MVP-LEAD-20261026-RECOVERY2", "MVP-PERMISSION-20261026-ASSIGNED",
)

# name, contact, title, email, area code, city, business type, status, account
CLIENTS = (
    ("Alderline Studio", "Maya Bennett", "Studio Director", "maya@alderline.test",
     "512", "Austin", "Professional Services", "active", "Design Operations"),
    ("Briarstone Fixtures", "Rafael Ortiz", "Operations Director", "rafael@briarstone.test",
     "817", "Fort Worth", "Manufacturing", "active", "Commercial Programs"),
    ("Fieldfare Market Co.", "Tessa Nguyen", "Retail Operations Manager", "tessa@fieldfare.test",
     "214", "Dallas", "Retail", "active", "Store Operations"),
    ("Juniper Vale Medical", "Elena Morales", "Practice Administrator", "elena@junipervale.test",
     "210", "San Antonio", "Healthcare", "active", "Clinic Operations"),
    ("Lantern Creek Learning", "Owen Brooks", "Program Director", "owen@lanterncreek.test",
     "512", "Round Rock", "Education", "active", "Program Services"),
    ("Ridgeline Signal", "Nina Patel", "Head of Operations", "nina@ridgelinesignal.test",
     "469", "Plano", "Technology", "prospect", "Implementation Services"),
)

# name, contact, title, email, area code, city, business type, stage, source, notes
LEADS = (
    ("Copper Fern Supply", "Celia Hart", "General Manager", "celia@copperfern.test",
     "940", "Denton", "Retail", "new", "Website", "Asked about coordinating quotes across two branches."),
    ("Red Mesa Fabrication", "Gabriel Soto", "Plant Manager", "gabriel@redmesafab.test",
     "915", "El Paso", "Manufacturing", "qualified", "Referral", "Needs a shared view of open bids before year-end."),
    ("Marigold Wellness Group", "Iris Chen", "Director of Operations", "iris@marigoldwellness.test",
     "713", "Houston", "Healthcare", "contacted", "Trade Show", "Requested a walkthrough for the intake team."),
    ("Oakspan Advisory", "Leah Ellis", "Managing Partner", "leah@oakspan.test",
     "512", "Austin", "Professional Services", "qualified", "Partner", "Comparing a pilot for eight account managers."),
    ("Silver Kestrel Networks", "Marcus Reed", "Sales Director", "marcus@silverkestrel.test",
     "972", "Richardson", "Technology", "new", "Website", "Interested in tracking project handoffs and renewals."),
    ("Wrenfield Academy", "Amara James", "Enrollment Director", "amara@wrenfield.test",
     "254", "Waco", "Education", "contacted", "Email Campaign", "Asked for examples of inquiry follow-ups."),
    ("Clearwater Ledger Co.", "Sofia Ramirez", "Client Services Manager", "sofia@clearwaterledger.test",
     "361", "Corpus Christi", "Finance", "lost", "Referral", "Chose to revisit the rollout next fiscal year."),
)

# project, company, status, value, start and end offsets, scope
PROJECTS = (
    ("Design Intake Rollout", "Alderline Studio", "won", 18400, -26, 24,
     "Standardize design briefs and approval checkpoints for the studio team."),
    ("Dealer Quote Portal", "Briarstone Fixtures", "won", 42750, -15, 47,
     "Give the dealer team one place to request quotes and check bid progress."),
    ("Holiday Store Launch", "Fieldfare Market Co.", "pending", 26200, 7, 43,
     "Coordinate signage, fixtures, and launch tasks across three locations."),
    ("Clinic Referral Workflow", "Juniper Vale Medical", "won", 31500, -21, 31,
     "Set up referral intake, ownership, and reminders for the clinic team."),
    ("Spring Enrollment Pilot", "Lantern Creek Learning", "pending", 14250, 18, 74,
     "Pilot a shared enrollment pipeline with two program coordinators."),
    ("Partner Onboarding Portal", "Ridgeline Signal", "pending", 28400, 12, 61,
     "Scope partner onboarding milestones and reporting for the first cohort."),
    ("Bid Tracking Pilot", "Red Mesa Fabrication", "pending", 19800, 14, 55,
     "Test bid stages and handoffs with the estimating team."),
    ("Vendor Catalog Refresh", "Fieldfare Market Co.", "lost", 9600, -55, -22,
     "Consolidate supplier catalogs for the merchandising team."),
)

# parent kind/name, summary, outcome, days since contact, follow-up day/time, status
INTERACTIONS = (
    ("lead", "Red Mesa Fabrication", "Send the pilot estimate",
     "Gabriel confirmed the estimating team will review a one-page scope.", 4, -1, 10, "pending"),
    ("client", "Fieldfare Market Co.", "Confirm store opening dates",
     "Tessa shared the draft rollout schedule and flagged a vendor dependency.", 3, 0, 10, "pending"),
    ("project", "Dealer Quote Portal", "Review the quote intake form",
     "Rafael sent three recent quote requests to use in the walkthrough.", 2, 0, 14, "pending"),
    ("lead", "Marigold Wellness Group", "Schedule the intake walkthrough",
     "Iris will invite the clinic coordinator and front desk lead.", 2, 3, 11, "pending"),
    ("project", "Design Intake Rollout", "Check in on the first approval cycle",
     "Maya signed off on the intake form and assigned a reviewer.", 1, 4, 13, "pending"),
    ("client", "Juniper Vale Medical", "Share the referral workflow checklist",
     "Elena confirmed the front desk team is ready for the pilot.", 1, 5, 15, "pending"),
    ("lead", "Wrenfield Academy", "Send enrollment examples",
     "Amara received sample inquiry and campus tour follow-up steps.", 5, -2, 13, "completed"),
    ("project", "Spring Enrollment Pilot", "Review pilot scope with Owen",
     "Agreed to start with the spring inquiry and campus visit queues.", 6, None, None, "pending"),
)


def require_staging():
    if os.getenv("FLY_APP_NAME") != APP or os.getenv("CRM_RLS_ENABLED") != "1":
        raise SystemExit("Refusing to run outside the staging Fly app with RLS enabled.")
    url = make_url(os.getenv("DATABASE_URL", "").replace("postgres://", "postgresql://", 1))
    if (url.get_backend_name() != "postgresql" or url.host not in DB_HOSTS
            or url.database != DATABASE or url.username != RUNTIME_ROLE
            or url.port not in (None, 5432)):
        raise SystemExit("Refusing to run against an unexpected database target.")


def demo_principal(session_factory):
    from app.models import User
    from app.services.database_context import auth_lookup
    from app.services.principal import Principal

    with session_factory() as session:
        auth_lookup(session, email=ADMIN_EMAIL)
        if session.bind.dialect.name == "postgresql":
            session.execute(text("SET TRANSACTION READ ONLY"))
        user = session.query(User).filter_by(email=ADMIN_EMAIL, is_active=True).one()
        principal = Principal(user.id, user.tenant_id, [role.name for role in user.roles])
        if not principal.is_admin:
            raise RuntimeError("The staging demo login must be an administrator.")
        return principal


def fixture(session, model, key, fields):
    row = session.query(model).filter_by(**key).one_or_none()
    if row is None:
        row = model(**key)
        session.add(row)
    for field, value in fields.items():
        setattr(row, field, value)
    session.flush()
    return row


def old_trial_leads(session, tenant_id):
    from app.models import Lead

    return session.query(Lead).filter(
        Lead.tenant_id == tenant_id, Lead.deleted_at.is_(None),
        or_(Lead.name.in_(TRIAL_NAMES),
            (Lead.id == 17) & (Lead.created_by == 2) & (Lead.name == "SessionLoom")),
    )


def refresh(session, principal, tenant, now, today):
    from app.models import Account, ActivityLog, ActivityType, Client, FollowUpStatus, Interaction, Lead, Project
    from app.services.leads import LeadService

    def days_ago(days):
        return now - timedelta(days=days)

    tenant_key = {"tenant_id": tenant.id}
    owned = {"created_by": principal.user_id, "assigned_to": principal.user_id,
             "deleted_at": None, "deleted_by": None}
    parents = {}
    for index, (name, person, title, email, area, city, kind, status, account) in enumerate(CLIENTS):
        client = fixture(session, Client, {**tenant_key, "email": email}, {
            **owned, "name": name, "contact_person": person, "contact_title": title,
            "phone": f"{area}555{120 + index:04d}", "city": city, "state": "TX",
            "type": kind, "status": status, "created_at": days_ago(40 + index * 6),
        })
        parents["client", name] = client
        fixture(session, Account, {**tenant_key, "account_number": f"PS-{tenant.id}-{4201 + index}"}, {
            "client_id": client.id, "account_name": account, "status": "active",
            "opened_on": days_ago(32 + index * 6),
        })

    statuses = tenant.config["leads"]["statuses"]
    stage_names = {
        "new": "new" if "new" in statuses else "open",
        "contacted": "contacted" if "contacted" in statuses else "proposal",
        "qualified": "qualified", "lost": "lost",
    }
    if not set(stage_names.values()).issubset(statuses):
        raise RuntimeError("Unexpected staging lead pipeline configuration.")
    for index, (name, person, title, email, area, city, kind, status, source, notes) in enumerate(LEADS):
        parents["lead", name] = fixture(session, Lead, {**tenant_key, "email": email}, {
            **owned, "name": name, "contact_person": person, "contact_title": title,
            "phone": f"{area}555{140 + index:04d}", "city": city, "state": "TX",
            "type": kind, "lead_status": stage_names[status], "lead_source": source,
            "notes": notes, "created_at": days_ago(3 + index),
        })

    for index, (name, company, status, value, start, end, description) in enumerate(PROJECTS):
        parent_kind = "lead" if ("lead", company) in parents else "client"
        parent = parents[parent_kind, company]
        parents["project", name] = fixture(session, Project, {
            **tenant_key, "project_name": name, f"{parent_kind}_id": parent.id,
        }, {
            **owned, "project_status": status, "project_worth": value,
            "project_description": description, "type": parent.type,
            "primary_contact_name": parent.contact_person, "primary_contact_title": parent.contact_title,
            "primary_contact_email": parent.email, "primary_contact_phone": parent.phone,
            "created_at": days_ago(4 + index), "project_start": now + timedelta(days=start),
            "project_end": now + timedelta(days=end),
        })

    for kind, name, summary, outcome, age, due, hour, status in INTERACTIONS:
        parent = parents[kind, name]
        follow_up = None if due is None else datetime.combine(today + timedelta(days=due), time(hour))
        fixture(session, Interaction, {**tenant_key, f"{kind}_id": parent.id, "summary": summary}, {
            "contact_date": days_ago(age), "outcome": outcome, "follow_up": follow_up,
            "followup_status": FollowUpStatus(status),
        })
        fixture(session, ActivityLog, {
            **tenant_key, "user_id": principal.user_id, "entity_type": kind, "entity_id": parent.id,
            "action": ActivityType.edited, "description": f"Logged interaction: {summary}",
        }, {"timestamp": days_ago(age)})

    lead_service = LeadService(session, principal)
    trial_ids = [lead.id for lead in old_trial_leads(session, tenant.id).order_by(Lead.id).all()]
    for lead_id in trial_ids:
        lead_service.delete(lead_id)
    return trial_ids


def verify(session, principal, today):
    from app.services.accounts import AccountService
    from app.services.activity import ActivityService
    from app.services.clients import ClientService
    from app.services.interactions import InteractionService
    from app.services.leads import LeadService
    from app.services.projects import ProjectService

    leads = LeadService(session, principal).list_mine(per_page=100)["leads"]
    clients = ClientService(session, principal).list_mine(per_page=100)["clients"]
    accounts = AccountService(session, principal).list_visible()
    projects = ProjectService(session, principal).list_mine(per_page=100)["projects"]
    interactions = InteractionService(session, principal).list_visible()["interactions"]
    activity = ActivityService(session, principal).recent()
    due_dates = {
        row["summary"]: datetime.fromisoformat(row["follow_up"]).date()
        for row in interactions if row["follow_up"] and row["followup_status"] != "completed"
    }
    groups = {"overdue": 0, "today": 0, "upcoming": 0}
    for _, _, summary, _, _, due, _, status in INTERACTIONS:
        if due is None or status == "completed":
            continue
        if due_dates.get(summary) != today + timedelta(days=due):
            raise RuntimeError(f"Dashboard follow-up missing or out of date: {summary}")
        groups["overdue" if due < 0 else "today" if due == 0 else "upcoming"] += 1

    for rows, field, fixtures, index in (
        (leads, "name", LEADS, 0), (clients, "name", CLIENTS, 0),
        (accounts, "account_name", CLIENTS, 8), (projects, "project_name", PROJECTS, 0),
        (interactions, "summary", INTERACTIONS, 2),
    ):
        if {row[index] for row in fixtures} - {row[field] for row in rows}:
            raise RuntimeError(f"Missing staging fixtures for {field}.")
    if old_trial_leads(session, principal.tenant_id).count():
        raise RuntimeError("Old connector test leads are still active.")
    if not {row[1] for row in INTERACTIONS} & {row["name"] for row in activity}:
        raise RuntimeError("No demo activity is visible on the dashboard.")
    print(f"Visible: {len(leads)} leads, {len(clients)} clients, {len(accounts)} accounts, "
          f"{len(projects)} projects, {len(interactions)} recent interactions.")
    print(f"Demo follow-ups: {groups['overdue']} overdue, {groups['today']} today, "
          f"{groups['upcoming']} upcoming ({today}, America/Chicago).")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Refresh the staging screenshot fixtures.")
    args = parser.parse_args()
    require_staging()

    from app.database import SessionLocal
    from app.models import Tenant
    from app.services.database_context import bind_principal

    principal = demo_principal(SessionLocal)
    now = datetime.now(DEMO_TIMEZONE)
    with SessionLocal.begin() as session:
        bind_principal(session, principal)
        if not args.apply:
            session.execute(text("SET TRANSACTION READ ONLY"))
        if tuple(session.execute(text("SELECT current_database(), current_user")).one()) != (DATABASE, RUNTIME_ROLE):
            raise RuntimeError("Unexpected staging database identity.")
        tenant = session.query(Tenant).filter_by(id=principal.tenant_id, slug=TENANT_SLUG, is_active=True).one()
        if args.apply:
            if not session.execute(text("SELECT pg_try_advisory_xact_lock(183095173, :tenant_id)"),
                                   {"tenant_id": tenant.id}).scalar_one():
                raise RuntimeError("Another staging refresh is already running.")
            utc_now = now.astimezone(timezone.utc).replace(tzinfo=None)
            trial_ids = refresh(session, principal, tenant, utc_now, now.date())
        verify(session, principal, now.date())
    if args.apply:
        print(f"Staging refresh committed. Trial leads moved to Trash: {trial_ids}.")


if __name__ == "__main__":
    main()
