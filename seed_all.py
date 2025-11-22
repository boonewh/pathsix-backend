from datetime import datetime, timezone, timedelta
from app.database import SessionLocal, Base, engine
from app.models import User, Role, Client, Lead, Project, Interaction, Account, Contact
from app.utils.auth_utils import hash_password

print("🗑️  Dropping all tables...")
Base.metadata.drop_all(bind=engine)
print("🔨 Creating all tables...")
Base.metadata.create_all(bind=engine)

session = SessionLocal()

try:
    print("🎭 Creating roles...")
    # Roles
    admin_role = Role(name="admin")
    user_role = Role(name="user")
    session.add_all([admin_role, user_role])
    session.flush()

    print("👥 Creating users...")
    # Users
    admin_user = User(
        email="admin@pathsixdesigns.com",
        password_hash=hash_password("admin123"),
        tenant_id=1,
        roles=[admin_role]
    )
    
    sales_manager = User(
        email="sarah.mitchell@pathsixdesigns.com",
        password_hash=hash_password("password123"),
        tenant_id=1,
        roles=[user_role]
    )
    
    field_rep = User(
        email="mike.torres@pathsixdesigns.com", 
        password_hash=hash_password("password123"),
        tenant_id=1,
        roles=[user_role]
    )
    
    project_coordinator = User(
        email="jen.davis@pathsixdesigns.com",
        password_hash=hash_password("password123"),
        tenant_id=1,
        roles=[user_role]
    )
    
    session.add_all([admin_user, sales_manager, field_rep, project_coordinator])
    session.flush()
    
    print("🏭 Creating clients...")

    ### ADMIN'S CLIENTS ###
    
    # Large Industrial Client
    client1 = Client(
        tenant_id=1,
        created_by=admin_user.id,
        assigned_to=sales_manager.id,
        name="Permian Basin Refinery Group",
        contact_person="Robert Chen",
        contact_title="Facilities Director",
        email="r.chen@permianbasin.com",
        phone="+12815550001",
        phone_label="work",
        secondary_phone="+12815550002",
        secondary_phone_label="mobile",
        address="4500 Industrial Parkway",
        city="Midland",
        state="TX",
        zip="79701",
        type="Oil & Gas",
        notes="Major refinery client. Specializes in secondary containment systems and tank linings. Annual contract worth $2M+.",
        created_at=datetime.now(timezone.utc) - timedelta(days=45)
    )
    session.add(client1)
    session.flush()

    # Food Processing Client
    client2 = Client(
        tenant_id=1,
        created_by=admin_user.id,
        assigned_to=field_rep.id,
        name="Southwest Food Processing LLC",
        contact_person="Maria Gonzalez",
        contact_title="Plant Manager",
        email="maria.g@swfoodprocessing.com",
        phone="+14325550010",
        phone_label="work",
        address="890 Processing Drive",
        city="Lubbock",
        state="TX",
        zip="79424",
        type="Food and Beverage",
        notes="Food-grade polyurea applications. FDA compliance required for all work.",
        created_at=datetime.now(timezone.utc) - timedelta(days=30)
    )
    session.add(client2)
    session.flush()

    ### SALES MANAGER'S CLIENTS ###
    
    # Construction Company
    client3 = Client(
        tenant_id=1,
        created_by=sales_manager.id,
        name="BigState Construction Partners",
        contact_person="Jake Morrison",
        contact_title="Project Superintendent", 
        email="jake.morrison@bigstateconstruction.com",
        phone="+19155550020",
        phone_label="work",
        secondary_phone="+19155550021",
        secondary_phone_label="mobile",
        address="1200 Commerce Street",
        city="El Paso",
        state="TX",
        zip="79901",
        type="Bridge",
        notes="Bridge deck waterproofing specialists. Currently working on I-10 expansion project.",
        created_at=datetime.now(timezone.utc) - timedelta(days=20)
    )
    session.add(client3)
    session.flush()

    ### FIELD REP'S CLIENTS ###
    
    # Municipal Client
    client4 = Client(
        tenant_id=1,
        created_by=field_rep.id,
        name="City of Abilene Public Works",
        contact_person="David Thompson",
        contact_title="Public Works Director",
        email="d.thompson@abilenetx.gov",
        phone="+13255550030",
        phone_label="work",
        address="555 Walnut Street",
        city="Abilene",
        state="TX",
        zip="79601",
        type="Culvert",
        notes="Municipal water infrastructure projects. Culvert lining and pipe rehabilitation.",
        created_at=datetime.now(timezone.utc) - timedelta(days=15)
    )
    session.add(client4)
    session.flush()

    print("📊 Creating accounts...")
    
    # Accounts for clients
    account1 = Account(
        tenant_id=1,
        client_id=client1.id,
        account_number="PBR-2025-001",
        account_name="Tank Farm Coatings Contract",
        notes="Annual service contract for tank maintenance and secondary containment.",
        opened_on=datetime.now(timezone.utc) - timedelta(days=40)
    )
    
    account2 = Account(
        tenant_id=1,
        client_id=client2.id,
        account_number="SWF-2025-001", 
        account_name="Food Grade Flooring Systems",
        notes="FDA-compliant flooring installation and maintenance.",
        opened_on=datetime.now(timezone.utc) - timedelta(days=25)
    )
    
    session.add_all([account1, account2])

    print("🎯 Creating leads...")

    ### VARIOUS LEADS ###
    
    # Hot Lead - Oil & Gas
    lead1 = Lead(
        tenant_id=1,
        created_by=sales_manager.id,
        assigned_to=field_rep.id,
        name="Eagle Ford Drilling Operations",
        contact_person="Carlos Ramirez", 
        contact_title="Operations Manager",
        email="cramirez@eagleforddrilling.com",
        phone="+12105550040",
        phone_label="work",
        secondary_phone="+12105550041",
        secondary_phone_label="mobile",
        address="7800 Energy Boulevard",
        city="San Antonio",
        state="TX",
        zip="78216",
        type="Oil & Gas",
        lead_status="open",
        notes="Needs containment systems for new drilling sites. Estimated 15 locations over next 6 months.",
        created_at=datetime.now(timezone.utc) - timedelta(days=8)
    )
    
    # Warm Lead - Infrastructure
    lead2 = Lead(
        tenant_id=1,
        created_by=field_rep.id,
        name="Trinity River Authority",
        contact_person="Rebecca Johnson",
        contact_title="Engineering Supervisor",
        email="r.johnson@trinityriver.org",
        phone="+18175550050",
        phone_label="work",
        address="1234 River Road",
        city="Fort Worth", 
        state="TX",
        zip="76102",
        type="Pipe",
        lead_status="open",
        notes="Water treatment facility pipe lining project. RFP expected Q3 2025.",
        created_at=datetime.now(timezone.utc) - timedelta(days=12)
    )
    
    # Cold Lead - Manufacturing  
    lead3 = Lead(
        tenant_id=1,
        created_by=project_coordinator.id,
        name="Texas Steel Fabrication",
        contact_person="Jim Walker",
        contact_title="Maintenance Supervisor",
        email="jwalker@texassteel.com",
        phone="+14095550060",
        phone_label="work",
        address="3300 Industrial Road",
        city="Beaumont",
        state="TX", 
        zip="77701",
        type="Tanks",
        lead_status="open",
        notes="Steel tank maintenance and coating. Budget approval pending for 2025.",
        created_at=datetime.now(timezone.utc) - timedelta(days=25)
    )
    
    # Converted Lead
    lead4 = Lead(
        tenant_id=1,
        created_by=sales_manager.id,
        name="Highway Solutions Inc",
        contact_person="Lisa Park",
        contact_title="Project Manager",
        email="lpark@highwaysolutions.com", 
        phone="+14325550070",
        phone_label="work",
        address="1500 Interstate Drive",
        city="Austin",
        state="TX",
        zip="78701",
        type="Bridge",
        lead_status="converted",
        converted_on=datetime.now(timezone.utc) - timedelta(days=5),
        notes="CONVERTED: Now client. Bridge deck waterproofing specialists.",
        created_at=datetime.now(timezone.utc) - timedelta(days=35)
    )
    
    session.add_all([lead1, lead2, lead3, lead4])
    session.flush()

    print("🚧 Creating projects...")

    ### PROJECTS WITH NEW CONTACT FIELDS ###
    
    # Client-linked project (no standalone contact info needed)
    project1 = Project(
        tenant_id=1,
        created_by=admin_user.id,
        client_id=client1.id,
        project_name="Refinery Tank Farm Phase 2",
        type="Oil & Gas",
        project_description="Secondary containment system installation for 12 storage tanks. Polyurea coating with chemical resistance.",
        project_status="pending",
        project_start=datetime(2025, 6, 15, tzinfo=timezone.utc),
        project_end=datetime(2025, 8, 30, tzinfo=timezone.utc),
        project_worth=185000,
        notes="Customer requires 24/7 security clearance. All work must be done during shutdown periods.",
        created_at=datetime.now(timezone.utc) - timedelta(days=20)
    )
    
    # Lead-linked project
    project2 = Project(
        tenant_id=1,
        created_by=sales_manager.id,
        lead_id=lead1.id,
        project_name="Eagle Ford Site Prep - Phase 1",
        type="Oil & Gas", 
        project_description="Containment pad installation for drilling equipment at 3 initial sites.",
        project_status="pending",
        project_start=datetime(2025, 7, 1, tzinfo=timezone.utc),
        project_end=datetime(2025, 7, 15, tzinfo=timezone.utc),
        project_worth=95000,
        notes="Weather-dependent timeline. Must coordinate with drilling schedule.",
        created_at=datetime.now(timezone.utc) - timedelta(days=15)
    )
    
    # STANDALONE PROJECT with contact fields - This is the key new feature!
    project3 = Project(
        tenant_id=1,
        created_by=field_rep.id,
        project_name="Confidential Petrochemical Facility",
        type="Secondary Containment",
        project_description="High-security chemical storage containment system. NDA required.",
        project_status="pending",
        project_start=datetime(2025, 8, 1, tzinfo=timezone.utc),
        project_end=datetime(2025, 9, 15, tzinfo=timezone.utc),
        project_worth=320000,
        # NEW: Standalone contact information
        primary_contact_name="Dr. Sarah Chen",
        primary_contact_title="Chemical Engineering Manager",
        primary_contact_email="s.chen@confidentialclient.com",
        primary_contact_phone="+17135550080",
        primary_contact_phone_label="work",
        notes="CONFIDENTIAL PROJECT. Contact via secure channels only. All personnel require security clearance.",
        created_at=datetime.now(timezone.utc) - timedelta(days=10)
    )
    
    # Another standalone project
    project4 = Project(
        tenant_id=1,
        created_by=project_coordinator.id,
        project_name="Municipal Water Treatment Upgrade",
        type="Pipe",
        project_description="Emergency pipe lining for water treatment facility after infrastructure failure.", 
        project_status="won",
        project_start=datetime(2025, 5, 20, tzinfo=timezone.utc),
        project_end=datetime(2025, 6, 5, tzinfo=timezone.utc),
        project_worth=78000,
        # NEW: Emergency contact information
        primary_contact_name="Mark Rodriguez",
        primary_contact_title="Emergency Response Coordinator",
        primary_contact_email="mrodriguez@citywater.gov",
        primary_contact_phone="+14695550090",
        primary_contact_phone_label="mobile",
        notes="EMERGENCY PROJECT. 24/7 contact availability required. Fast-track approval process.",
        created_at=datetime.now(timezone.utc) - timedelta(days=5)
    )
    
    # Lost project for demo purposes
    project5 = Project(
        tenant_id=1,
        created_by=sales_manager.id,
        project_name="Highway Overpass Deck Coating",
        type="Bridge", 
        project_description="Protective coating system for new highway overpass construction.",
        project_status="lost",
        project_worth=145000,
        # Contact info for lost bid
        primary_contact_name="Jennifer Liu",
        primary_contact_title="DOT Project Manager", 
        primary_contact_email="j.liu@txdot.gov",
        primary_contact_phone="+15125550100",
        primary_contact_phone_label="work",
        notes="LOST TO COMPETITOR. Lower bid by $15K. Maintain relationship for future opportunities.",
        created_at=datetime.now(timezone.utc) - timedelta(days=30)
    )
    
    session.add_all([project1, project2, project3, project4, project5])
    session.flush()

    print("📞 Creating interactions...")

    ### INTERACTIONS ###
    
    # Client interaction with follow-up
    interaction1 = Interaction(
        tenant_id=1,
        client_id=client1.id,
        contact_person="Robert Chen",
        email="r.chen@permianbasin.com",
        phone="+12815550001",
        contact_date=datetime.now(timezone.utc) - timedelta(days=3),
        summary="Phase 2 project kickoff meeting",
        outcome="Proceed with engineering drawings and timeline",
        notes="Client wants to accelerate timeline by 2 weeks. Discussed overtime rates and crew scheduling.",
        follow_up=datetime.now(timezone.utc) + timedelta(days=5)
    )
    
    # Lead interaction - urgent follow-up
    interaction2 = Interaction(
        tenant_id=1,
        lead_id=lead1.id,
        contact_person="Carlos Ramirez",
        email="cramirez@eagleforddrilling.com", 
        phone="+12105550040",
        contact_date=datetime.now(timezone.utc) - timedelta(days=1),
        summary="Site visit and technical requirements review",
        outcome="Submit formal proposal by Friday",
        notes="Customer is impressed with our track record. Main competitor is 10% higher on pricing. Need quick turnaround.",
        follow_up=datetime.now(timezone.utc) + timedelta(days=2)
    )
    
    # PROJECT interaction - new feature!
    interaction3 = Interaction(
        tenant_id=1,
        project_id=project3.id,
        contact_person="Dr. Sarah Chen",
        email="s.chen@confidentialclient.com",
        phone="+17135550080",
        contact_date=datetime.now(timezone.utc) - timedelta(hours=6),
        summary="Security clearance and NDA completion",
        outcome="All personnel cleared - project can proceed",
        notes="Security process took 3 weeks. All crew members now have facility access. Start date confirmed.",
        follow_up=datetime.now(timezone.utc) + timedelta(days=7)
    )
    
    # Another project interaction
    interaction4 = Interaction(
        tenant_id=1,
        project_id=project4.id,
        contact_person="Mark Rodriguez", 
        email="mrodriguez@citywater.gov",
        phone="+14695550090",
        contact_date=datetime.now(timezone.utc) - timedelta(hours=2),
        summary="Emergency response coordination call",
        outcome="Begin mobilization immediately",
        notes="Water main break affecting 50K residents. City council approved emergency spending. All hands on deck.",
        follow_up=datetime.now(timezone.utc) + timedelta(hours=12)
    )
    
    # Completed interaction
    interaction5 = Interaction(
        tenant_id=1,
        client_id=client2.id,
        contact_person="Maria Gonzalez",
        email="maria.g@swfoodprocessing.com",
        phone="+14325550010",
        contact_date=datetime.now(timezone.utc) - timedelta(days=7),
        summary="FDA compliance documentation review",
        outcome="All documentation approved",
        notes="FDA inspector was satisfied with our material certifications. Green light for production area work.",
        followup_status="completed"
    )
    
    session.add_all([interaction1, interaction2, interaction3, interaction4, interaction5])

    print("👥 Creating additional contacts...")
    
    ### ADDITIONAL CONTACTS ###
    
    # Multiple contacts for large client
    contact1 = Contact(
        tenant_id=1,
        client_id=client1.id,
        first_name="Jennifer",
        last_name="Walsh", 
        title="Safety Coordinator",
        email="j.walsh@permianbasin.com",
        phone="+12815550003",
        phone_label="work",
        secondary_phone="+12815550004", 
        secondary_phone_label="mobile",
        notes="Primary safety contact for all on-site work. Must approve all safety plans."
    )
    
    contact2 = Contact(
        tenant_id=1,
        client_id=client1.id,
        first_name="Michael",
        last_name="Foster",
        title="Procurement Manager", 
        email="m.foster@permianbasin.com",
        phone="+12815550005",
        phone_label="work",
        notes="Handles all purchase orders and contractor payments."
    )
    
    # Lead contact
    contact3 = Contact(
        tenant_id=1,
        lead_id=lead2.id,
        first_name="Tom",
        last_name="Bradley",
        title="Assistant Engineer",
        email="t.bradley@trinityriver.org", 
        phone="+18175550051",
        phone_label="work",
        notes="Technical point of contact for engineering specifications."
    )
    
    session.add_all([contact1, contact2, contact3])

    session.commit()
    
    print("\n🎉 Database seeded successfully!")
    print(f"📧 Admin login: admin@pathsixdesigns.com / admin123")
    print(f"👤 User logins:")
    print(f"   • sarah.mitchell@pathsixdesigns.com / password123")
    print(f"   • mike.torres@pathsixdesigns.com / password123") 
    print(f"   • jen.davis@pathsixdesigns.com / password123")
    print(f"\n📊 Created:")
    print(f"   • 4 Clients (with realistic contact info)")
    print(f"   • 4 Leads (various stages)")
    print(f"   • 5 Projects (including 3 STANDALONE projects with contact fields)")
    print(f"   • 5 Interactions (including 2 PROJECT interactions)")
    print(f"   • 2 Accounts")
    print(f"   • 3 Additional contacts")
    print(f"\n🚧 Key Features Demonstrated:")
    print(f"   • Standalone projects with primary_contact_* fields")
    print(f"   • Project interactions (new feature)")
    print(f"   • Mixed client/lead/project interactions")
    print(f"   • Realistic industrial/construction theme")
    print(f"   • Various project statuses (pending/won/lost)")

finally:
    session.close()