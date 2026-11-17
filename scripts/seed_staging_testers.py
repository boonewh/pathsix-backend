"""Create ordinary staging tester accounts in Staging Demo Co, each with their own records.

On the staging machine, with the tester list on stdin as JSON:
    {"testers": [{"email": "...", "name": "...", "password": "..."}]}
Passwords arrive only on stdin and are never printed. Existing testers keep
their password; their records are refreshed. Every tester is an ordinary user
who owns their own clients (with accounts and contacts), leads, projects and
interactions, so testing also proves each person sees only their own records.
"""
import json
import os
import sys
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parents[1]) if '__file__' in globals() else '/app')

APP = 'pathsixsolutions-backend-staging'
DATABASE = 'pathsixsolutions_backend_staging'
RUNTIME_ROLE = 'pathsix_crm_staging_runtime'
DB_HOSTS = {'pathsixsolutions-db-staging.flycast', 'pathsixsolutions-db-staging.internal'}
ADMIN_EMAIL = 'admin@staging.test'
TENANT_SLUG = 'staging'
LOCAL = ZoneInfo('America/Chicago')  # Interaction dates are local wall-clock times.

# Per tester: two clients, three leads. (name, contact, title, area code, city, type)
COMPANIES = [
    [('Bluebonnet Logistics', 'Carla Diaz', 'Operations Manager', '512', 'Austin', 'Logistics'),
     ('Pecan Grove Dental', 'Dr. Sam Wu', 'Practice Owner', '210', 'San Antonio', 'Healthcare'),
     ('Tidewater Roofing', 'Ben Harlan', 'Owner', '361', 'Corpus Christi', 'Construction'),
     ('Lone Oak Coffee Co.', 'Priya Shah', 'Founder', '254', 'Waco', 'Retail'),
     ('Granite Peak Insurance', 'Marcus Lee', 'Agency Principal', '972', 'Plano', 'Finance')],
    [('Cedar Hollow Builders', 'Hank Morales', 'General Contractor', '817', 'Fort Worth', 'Construction'),
     ('Starlight Event Rentals', 'Jenna Cole', 'Sales Manager', '214', 'Dallas', 'Hospitality'),
     ('Riverbend Veterinary', 'Dr. Ana Price', 'Medical Director', '512', 'San Marcos', 'Healthcare'),
     ('Ironwood Machining', 'Luis Ortega', 'Plant Manager', '432', 'Midland', 'Manufacturing'),
     ('Sunfield Solar', 'Kara Nguyen', 'Business Development', '713', 'Houston', 'Energy')],
    [('Magnolia Home Health', 'Rosa Jimenez', 'Administrator', '936', 'Conroe', 'Healthcare'),
     ('Prairie Wind Farms', 'Doug Becker', 'Operations Lead', '806', 'Amarillo', 'Agriculture'),
     ('Harbor Light Realty', 'Nicole Grant', 'Broker', '409', 'Galveston', 'Real Estate'),
     ('Mesquite Smokehouse', 'Travis Bell', 'Owner', '915', 'El Paso', 'Hospitality'),
     ('Northstar IT Services', 'Omar Haddad', 'Managing Director', '469', 'Frisco', 'Technology')],
    [('Canyon Ridge Academy', 'Laura Pierce', 'Head of School', '325', 'Abilene', 'Education'),
     ('Gulf Coast Marine', 'Ray Tanner', 'Service Manager', '281', 'Pasadena', 'Marine'),
     ('Bright Path Tutoring', 'Mei Lin', 'Director', '512', 'Round Rock', 'Education'),
     ('Redstone Fitness', 'Jordan Ellis', 'Owner', '940', 'Denton', 'Fitness'),
     ('Silver Sage Senior Living', 'Grace Howard', 'Executive Director', '830', 'New Braunfels', 'Healthcare')],
]
LEAD_STAGES = (('new', 'Website', 'Asked for pricing and a short demo.'),
               ('contacted', 'Referral', 'Had an intro call; wants a proposal next week.'),
               ('qualified', 'Trade Show', 'Budget approved; comparing two vendors.'))


def require_staging():
    if os.getenv('FLY_APP_NAME') != APP or os.getenv('CRM_RLS_ENABLED') != '1':
        raise SystemExit('Refusing to run outside the staging Fly app with RLS enabled.')
    url = make_url(os.getenv('DATABASE_URL', '').replace('postgres://', 'postgresql://', 1))
    if url.host not in DB_HOSTS or url.database != DATABASE or url.username != RUNTIME_ROLE:
        raise SystemExit('Refusing to run against an unexpected database target.')


def principal_for(session_factory, email):
    from app.models import User
    from app.services.database_context import auth_lookup
    from app.services.principal import Principal
    with session_factory() as session:
        auth_lookup(session, email=email)
        user = session.query(User).filter_by(email=email, is_active=True).one()
        return Principal(user.id, user.tenant_id, [role.name for role in user.roles])


def fixture(session, model, key, fields):
    row = session.query(model).filter_by(**key).one_or_none()
    if row is None:
        row = model(**key)
        session.add(row)
    for field, value in fields.items():
        setattr(row, field, value)
    session.flush()
    return row


def seed_records(session, tenant, user_id, companies, now):
    from app.models import Account, Client, Contact, FollowUpStatus, Interaction, Lead, Project
    key, owned = {'tenant_id': tenant.id}, {'created_by': user_id, 'assigned_to': user_id, 'deleted_at': None}
    today, stored_now = now.date(), now.replace(tzinfo=None)
    statuses = tenant.config['leads']['statuses']
    records = {}
    for index, (name, person, title, area, city, kind) in enumerate(companies):
        first, last = person.replace('Dr. ', '').split(' ', 1)
        domain = name.lower().replace('.', '').replace(' ', '') + '.test'
        common = {'contact_person': person, 'contact_title': title, 'phone': f'+1{area}555{1200 + index:04d}',
                  'city': city, 'state': 'TX', 'type': kind, 'email': f'{first.lower()}@{domain}'}
        if index < 2:
            row = fixture(session, Client, {**key, 'name': name}, {
                **owned, **common, 'status': 'active', 'address': f'{100 + index * 15} Main St', 'zip': '78701',
                'notes': f'Long-time customer in {city}.', 'created_at': stored_now - timedelta(days=60 - index * 10)})
            fixture(session, Account, {**key, 'account_number': f'T-{user_id}-{index + 1:03d}'}, {
                'client_id': row.id, 'account_name': f'{name} main account', 'status': 'active',
                'opened_on': stored_now - timedelta(days=45)})
            fixture(session, Contact, {**key, 'client_id': row.id, 'first_name': first}, {
                'last_name': last, 'title': title, 'email': common['email'], 'phone': common['phone']})
            records['client', index] = row
        else:
            stage, source, notes = LEAD_STAGES[index - 2]
            row = fixture(session, Lead, {**key, 'name': name}, {
                **owned, **common, 'lead_status': stage if stage in statuses else statuses[0],
                'lead_source': source, 'notes': notes, 'created_at': stored_now - timedelta(days=index * 3)})
            fixture(session, Contact, {**key, 'lead_id': row.id, 'first_name': first}, {
                'last_name': last, 'title': title, 'email': common['email'], 'phone': common['phone']})
            records['lead', index] = row
    projects = [('client', 0, 'Fleet Scheduling Upgrade', 'won', 24500), ('lead', 4, 'Pilot Rollout', 'pending', 12800)]
    for kind, index, title, status, worth in projects:
        parent = records[kind, index]
        records['project', title] = fixture(session, Project, {**key, f'{kind}_id': parent.id,
                                                               'project_name': f'{parent.name} {title}'}, {
            **owned, 'project_status': status, 'project_worth': worth, 'type': parent.type,
            'project_description': f'{title} for {parent.name}.', 'primary_contact_name': parent.contact_person,
            'primary_contact_email': parent.email, 'project_start': stored_now - timedelta(days=10),
            'project_end': stored_now + timedelta(days=50)})
    follow_ups = [(('lead', 2), 'Send pricing sheet', 0, 10), (('client', 1), 'Quarterly check-in call', 1, 14),
                  (('lead', 3), 'Follow up on proposal', 3, 9), (('client', 0), 'Confirm renewal details', -2, 11)]
    for (kind, index), summary, day, hour in follow_ups:
        parent = records[kind, index]
        fixture(session, Interaction, {**key, f'{kind}_id': parent.id, 'summary': summary}, {
            'contact_date': stored_now - timedelta(days=2), 'outcome': f'Spoke with {parent.contact_person}.',
            'follow_up': datetime.combine(today + timedelta(days=day), time(hour)),
            'followup_status': FollowUpStatus.pending, 'contact_person': parent.contact_person})
    return records


def main():
    require_staging()
    testers = json.loads(sys.stdin.readline())['testers']
    if len(testers) > len(COMPANIES):
        raise SystemExit('Add company pools before seeding more testers.')
    from app.database import SessionLocal
    from app.models import Tenant, User
    from app.services.database_context import bind_principal
    from app.services.users import UserService
    admin = principal_for(SessionLocal, ADMIN_EMAIL)
    if not admin.is_admin:
        raise SystemExit('The staging admin must be an administrator.')
    now = datetime.now(LOCAL)
    report = []
    with SessionLocal.begin() as session:
        bind_principal(session, admin)
        if tuple(session.execute(text('SELECT current_database(), current_user')).one()) != (DATABASE, RUNTIME_ROLE):
            raise RuntimeError('Unexpected staging database identity.')
        tenant = session.query(Tenant).filter_by(id=admin.tenant_id, slug=TENANT_SLUG, is_active=True).one()
        for tester, companies in zip(testers, COMPANIES):
            # Login lowercases the typed email and matches exactly, so store it lowercase.
            tester['email'] = tester['email'].strip().lower()
            user = session.query(User).filter_by(email=tester['email']).one_or_none()
            created = user is None
            if created:
                UserService(session, admin).create({'email': tester['email'], 'password': tester['password'],
                                                    'roles': ['user']})
                user = session.query(User).filter_by(email=tester['email']).one()
            if user.tenant_id != tenant.id or any(role.name == 'admin' for role in user.roles):
                raise RuntimeError('Tester accounts must be ordinary users in Staging Demo Co.')
            seed_records(session, tenant, user.id, companies, now)
            report.append({'email': user.email, 'user_id': user.id, 'created': created})
    # Verify as each tester: they see exactly their own records.
    from app.models import Client, Interaction, Lead, Project
    from app.services.access import interaction_access_filter, owned_record_filter, project_access_filter
    for entry in report:
        tester = principal_for(SessionLocal, entry['email'])
        with SessionLocal() as session:
            bind_principal(session, tester)
            session.execute(text('SET TRANSACTION READ ONLY'))
            entry['visible'] = {
                'clients': session.query(Client).filter(owned_record_filter(Client, tester)).count(),
                'leads': session.query(Lead).filter(owned_record_filter(Lead, tester)).count(),
                'projects': session.query(Project).filter(Project.tenant_id == tester.tenant_id,
                    Project.deleted_at.is_(None), project_access_filter(tester)).count(),
                'interactions': session.query(Interaction).filter(Interaction.tenant_id == tester.tenant_id,
                    interaction_access_filter(tester)).count(),
                'admin': tester.is_admin}
    print(json.dumps({'tenant': TENANT_SLUG, 'testers': report}))


if __name__ == '__main__':
    main()
