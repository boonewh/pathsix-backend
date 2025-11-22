# seed_test_data.py

from datetime import datetime, timedelta, timezone
from app.database import SessionLocal, Base, engine
from app.models import User, Role, Client, Lead, Project, Interaction, Contact
from app.utils.auth_utils import hash_password

print("🗑️ Dropping all tables...")
Base.metadata.drop_all(bind=engine)
print("🔨 Creating all tables...")
Base.metadata.create_all(bind=engine)

session = SessionLocal()

try:
    print("🎭 Creating roles...")
    admin_role = Role(name="admin")
    user_role = Role(name="user")
    session.add_all([admin_role, user_role])
    session.flush()

    print("👥 Creating users...")
    admin = User(email="admin@demo.com", password_hash=hash_password("admin123"), tenant_id=1, roles=[admin_role])
    user1 = User(email="user1@demo.com", password_hash=hash_password("user123"), tenant_id=1, roles=[user_role])
    user2 = User(email="user2@demo.com", password_hash=hash_password("user123"), tenant_id=1, roles=[user_role])
    session.add_all([admin, user1, user2])
    session.flush()

    users = [
        ("Admin One", admin),
        ("User One", user1),
        ("User Two", user2),
    ]

    print("🏢 Creating clients, leads, interactions, contacts...")
    for label, user in users:
        # Client
        client = Client(
            tenant_id=1,
            name=f"{label} Client",
            created_by=user.id,
            contact_person=f"{label} Contact",
            email=f"{user.email.replace('@', '.client@')}",
            phone="+10000000001",
            address="123 Main St",
            city="Demo City",
            state="TX",
            zip="75001",
            type="Oil & Gas",
            created_at=datetime.now(timezone.utc),
        )
        session.add(client)
        session.flush()

        # Lead 1
        lead1 = Lead(
            tenant_id=1,
            name=f"{label} Lead",
            created_by=user.id,
            contact_person=f"{label} Lead Contact",
            email=f"{user.email.replace('@', '.lead1@')}",
            phone="+10000000002",
            address="456 Elm St",
            city="Demo City",
            state="TX",
            zip="75002",
            type="Bridge",
            lead_status="open",
            created_at=datetime.now(timezone.utc),
        )

        # Lead 2
        lead2 = Lead(
            tenant_id=1,
            name=f"{label} Lead 2",
            created_by=user.id,
            contact_person=f"{label} Lead2 Contact",
            email=f"{user.email.replace('@', '.lead2@')}",
            phone="+10000000003",
            address="789 Oak St",
            city="Demo City",
            state="TX",
            zip="75003",
            type="Pipe",
            lead_status="open",
            created_at=datetime.now(timezone.utc),
        )

                # Lead 1
        lead3 = Lead(
            tenant_id=1,
            name=f"{label} Lead 3",
            created_by=user.id,
            contact_person=f"{label} Lead Contact",
            email=f"{user.email.replace('@', '.lead1@')}",
            phone="+10000000002",
            address="456 Elm St",
            city="Demo City",
            state="TX",
            zip="75002",
            type="Bridge",
            lead_status="open",
            created_at=datetime.now(timezone.utc),
        )

        # Lead 2
        lead4 = Lead(
            tenant_id=1,
            name=f"{label} Lead 4",
            created_by=user.id,
            contact_person=f"{label} Lead2 Contact",
            email=f"{user.email.replace('@', '.lead2@')}",
            phone="+10000000003",
            address="789 Oak St",
            city="Demo City",
            state="TX",
            zip="75003",
            type="Pipe",
            lead_status="open",
            created_at=datetime.now(timezone.utc),
        )

                # Lead 1
        lead5 = Lead(
            tenant_id=1,
            name=f"{label} Lead 5",
            created_by=user.id,
            contact_person=f"{label} Lead Contact",
            email=f"{user.email.replace('@', '.lead1@')}",
            phone="+10000000002",
            address="456 Elm St",
            city="Demo City",
            state="TX",
            zip="75002",
            type="Bridge",
            lead_status="open",
            created_at=datetime.now(timezone.utc),
        )

        # Lead 2
        lead6 = Lead(
            tenant_id=1,
            name=f"{label} Lead 6",
            created_by=user.id,
            contact_person=f"{label} Lead2 Contact",
            email=f"{user.email.replace('@', '.lead2@')}",
            phone="+10000000003",
            address="789 Oak St",
            city="Demo City",
            state="TX",
            zip="75003",
            type="Pipe",
            lead_status="open",
            created_at=datetime.now(timezone.utc),
        )

                # Lead 1
        lead7 = Lead(
            tenant_id=1,
            name=f"{label} Lead 7",
            created_by=user.id,
            contact_person=f"{label} Lead Contact",
            email=f"{user.email.replace('@', '.lead1@')}",
            phone="+10000000002",
            address="456 Elm St",
            city="Demo City",
            state="TX",
            zip="75002",
            type="Bridge",
            lead_status="open",
            created_at=datetime.now(timezone.utc),
        )

        # Lead 2
        lead8 = Lead(
            tenant_id=1,
            name=f"{label} Lead 8",
            created_by=user.id,
            contact_person=f"{label} Lead2 Contact",
            email=f"{user.email.replace('@', '.lead2@')}",
            phone="+10000000003",
            address="789 Oak St",
            city="Demo City",
            state="TX",
            zip="75003",
            type="Pipe",
            lead_status="open",
            created_at=datetime.now(timezone.utc),
        )

        session.add_all([lead1, lead2, lead3, lead4, lead5, lead6, lead7, lead8])
        session.flush()

        # Interactions
        session.add_all([
            Interaction(
                tenant_id=1,
                client_id=client.id,
                contact_person=client.contact_person,
                email=client.email,
                phone=client.phone,
                contact_date=datetime.now(timezone.utc),
                summary=f"{label} Client Interaction",
                outcome="Initial contact",
                notes=f"Interaction with {label} client",
                follow_up=datetime.now(timezone.utc) + timedelta(days=3),
            ),
            Interaction(
                tenant_id=1,
                lead_id=lead1.id,
                contact_person=lead1.contact_person,
                email=lead1.email,
                phone=lead1.phone,
                contact_date=datetime.now(timezone.utc),
                summary=f"{label} Lead Interaction",
                outcome="Requested follow-up",
                notes=f"Interaction with {label} lead",
                follow_up=datetime.now(timezone.utc) + timedelta(days=5),
            ),
            Interaction(
                tenant_id=1,
                lead_id=lead2.id,
                contact_person=lead2.contact_person,
                email=lead2.email,
                phone=lead2.phone,
                contact_date=datetime.now(timezone.utc),
                summary=f"{label} Lead 2 Interaction",
                outcome="Intro call",
                notes=f"Introductory discussion with {label} lead 2",
                follow_up=datetime.now(timezone.utc) + timedelta(days=7),
            ),
        ])

        # Contacts
        session.add_all([
            Contact(
                tenant_id=1,
                client_id=client.id,
                first_name=f"{label} Extra",
                last_name="Contact",
                email=f"{user.email.replace('@', '.extra.client@')}",
                phone="+10000000004",
                title="Secondary Contact"
            ),
            Contact(
                tenant_id=1,
                lead_id=lead1.id,
                first_name=f"{label} Extra",
                last_name="Lead1",
                email=f"{user.email.replace('@', '.extra.lead1@')}",
                phone="+10000000005",
                title="Backup Lead Contact"
            ),
            Contact(
                tenant_id=1,
                lead_id=lead2.id,
                first_name=f"{label} Extra",
                last_name="Lead2",
                email=f"{user.email.replace('@', '.extra.lead2@')}",
                phone="+10000000006",
                title="Backup Lead2 Contact"
            ),
        ])

        # Projects
        session.add_all([
            Project(
                tenant_id=1,
                created_by=user.id,
                client_id=client.id,
                project_name=f"Project for {label} Client",
                project_status="pending",
                type="Tank",
                project_description="Demo client project",
                project_worth=50000,
                created_at=datetime.now(timezone.utc),
            ),
            Project(
                tenant_id=1,
                created_by=user.id,
                lead_id=lead1.id,
                project_name=f"Project for {label} Lead",
                project_status="won",
                type="Containment",
                project_description="Demo lead project",
                project_worth=75000,
                created_at=datetime.now(timezone.utc),
            )
        ])

    print("🚧 Creating unattached projects...")
    session.add_all([
        Project(
            tenant_id=1,
            created_by=admin.id,
            project_name="Unattached Project A",
            project_status="pending",
            type="Bridge",
            project_description="Standalone project A",
            project_worth=12345,
            created_at=datetime.now(timezone.utc),
        ),
        Project(
            tenant_id=1,
            created_by=user2.id,
            project_name="Unattached Project B",
            project_status="lost",
            type="Other",
            project_description="Standalone project B",
            project_worth=67890,
            created_at=datetime.now(timezone.utc),
        )
    ])

    session.commit()
    print("✅ Done! Test data loaded.")
    print("Logins:")
    print("  admin@demo.com / admin123")
    print("  user1@demo.com / user123")
    print("  user2@demo.com / user123")

finally:
    session.close()
