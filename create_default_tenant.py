"""
================================================================================
QUICK TENANT + ADMIN SETUP (WITH DEFAULT CONFIG)
================================================================================

################################################################################
#                     HOW TO RUN THIS IN PRODUCTION                            #
################################################################################
#
# This script must connect to the PRODUCTION database to create tenants.
# You have TWO options:
#
# ══════════════════════════════════════════════════════════════════════════════
# OPTION 1: SSH INTO FLY.IO (RECOMMENDED)
# ══════════════════════════════════════════════════════════════════════════════
#
# Step 1: Deploy latest code (if you made changes to this file)
#         $ flyctl deploy
#
# Step 2: Start the production machine (it may be auto-stopped)
#         $ flyctl machine start d894111b636938 -a pathsixsolutions-backend
#
# Step 3: SSH into the production server
#         $ flyctl ssh console -a pathsixsolutions-backend
#
# Step 4: Run this script using the venv Python
#         # /venv/bin/python create_default_tenant.py
#
# Step 5: Follow the prompts (enter tenant name, slug, admin email/password)
#
# Step 6: Exit SSH when done
#         # exit
#
# ══════════════════════════════════════════════════════════════════════════════
# OPTION 2: DATABASE PROXY (Run locally against production DB)
# ══════════════════════════════════════════════════════════════════════════════
#
# Step 1: In terminal 1, start the database proxy
#         $ flyctl proxy 5432 -a pathsixsolutions-db
#         (Leave this running - it will say "Proxying local port 5432 to...")
#
# Step 2: In terminal 2, set DATABASE_URL to point to the proxy
#         Windows CMD:
#         > set DATABASE_URL=postgresql://postgres:YOUR_DB_PASSWORD@localhost:5432/pathsixsolutions_backend
#
#         Windows PowerShell:
#         > $env:DATABASE_URL="postgresql://postgres:YOUR_DB_PASSWORD@localhost:5432/pathsixsolutions_backend"
#
#         Mac/Linux:
#         $ export DATABASE_URL="postgresql://postgres:YOUR_DB_PASSWORD@localhost:5432/pathsixsolutions_backend"
#
# Step 3: Run this script locally
#         $ python create_default_tenant.py
#
# Step 4: Follow the prompts (enter tenant name, slug, admin email/password)
#
# Step 5: Close the proxy in terminal 1 (Ctrl+C)
#
# NOTE: To find your DB password, check Fly.io secrets:
#       $ flyctl secrets list -a pathsixsolutions-backend
#       The DATABASE_URL secret contains the password.
#
################################################################################

WHAT THIS SCRIPT DOES:
    1. Creates a new TENANT with DEFAULT configuration
    2. Creates an ADMIN USER for that tenant
    All in one step, with interactive prompts.

WHAT THIS SCRIPT DOES NOT DO:
    - Does NOT let you customize the config (uses defaults below)
    - For custom configs, use create_custom_tenant.py instead

--------------------------------------------------------------------------------
FAQ - READ THIS BEFORE RUNNING
--------------------------------------------------------------------------------

Q: What's the difference between this and create_custom_tenant.py?
A: - create_custom_tenant.py: CUSTOM config, edit file first, then run
   - THIS SCRIPT (create_default_tenant.py): DEFAULT config, just run and answer prompts

Q: When should I use this script?
A: When you want to quickly onboard a new customer with standard/default settings.
   They can customize later, or you can edit the database directly.

Q: When should I use create_custom_tenant.py instead?
A: When the customer needs custom configuration from day one:
   - Custom lead statuses (not the defaults)
   - Custom business types (industry-specific)
   - Custom labels (e.g., "Prospect" instead of "Lead")

Q: What is a "tenant" vs a "user"?
A: - TENANT: The organization/company using your CRM (e.g., "ASFI")
   - USER: A person who logs in (e.g., boonewh@pathsixdesigns.com)
   Multiple users can belong to one tenant. They all share the same config.

Q: Where does the config get stored?
A: In the DATABASE, in the "tenants" table. The frontend receives it on login.

Q: Can I edit the DEFAULT_TENANT_CONFIG below?
A: Yes! If you want to change what NEW tenants get by default, edit it below.
   This won't affect existing tenants - their config is already in the database.

--------------------------------------------------------------------------------
HOW TO USE
--------------------------------------------------------------------------------

1. Run: python create_default_tenant.py
2. Enter the tenant name (e.g., "Acme Corp")
3. Enter a slug (e.g., "acme") - lowercase, no spaces
4. Enter admin email and password
5. Done! The admin can now log in.

================================================================================
"""

from app.models import User, Role, Tenant
from app.database import SessionLocal
from app.utils.auth_utils import hash_password

# === DEFAULT CONFIG TEMPLATE ===
# New tenants created with THIS SCRIPT get this config.
# Edit this if you want to change the defaults for future tenants.
# (This won't affect tenants already in the database.)
DEFAULT_TENANT_CONFIG = {
    "branding": {
        "companyName": "",  # Will be set from tenant name
        "primaryColor": "#2563eb",
        "secondaryColor": "#64748b",
    },
    "labels": {
        "client": "Client",
        "lead": "Lead",
        "project": "Project",
        "interaction": "Interaction",
    },
    "leads": {
        "statuses": ["new", "contacted", "qualified", "lost", "converted"],
        "statusConfig": {
            "colors": {
                "new": "bg-yellow-100 text-yellow-800",
                "contacted": "bg-blue-100 text-blue-800",
                "qualified": "bg-orange-100 text-orange-800",
                "lost": "bg-red-100 text-red-800",
                "converted": "bg-green-100 text-green-800"
            },
            "icons": {
                "new": "circle-yellow",
                "contacted": "phone",
                "qualified": "circle-orange",
                "lost": "circle-red",
                "converted": "circle-green"
            },
            "labels": {
                "new": "New",
                "contacted": "Contacted",
                "qualified": "Qualified",
                "lost": "Lost",
                "converted": "Converted"
            }
        },
        "sources": [
            "Website", "Referral", "Cold Call", "Email Campaign",
            "Social Media", "Trade Show", "Advertisement", "Partner", "Other"
        ],
        "temperatures": ["hot", "warm", "cold"],
        "temperatureConfig": {
            "colors": {
                "hot": "text-red-600",
                "warm": "text-orange-600",
                "cold": "text-blue-600"
            },
            "icons": {
                "hot": "fire",
                "warm": "sun",
                "cold": "snowflake"
            }
        }
    },
    "businessTypes": [
        "None", "Professional Services", "Technology", "Manufacturing",
        "Retail", "Healthcare", "Finance", "Education", "Other"
    ],
    "regional": {
        "currency": "USD",
        "currencySymbol": "$",
        "dateFormat": "MM/DD/YYYY",
        "phoneFormat": "US",
        "addressFormat": "US"
    },
    "features": {
        "showTemperature": True,
        "showLeadScore": False,
        "showSource": True,
        "enableProjectStandalone": True,
        "enableBulkOperations": True,
        "enableAdvancedFilters": True,
        "enableDataExport": True
    },
    "defaults": {
        "leadsPerPage": 10,
        "clientsPerPage": 10,
        "projectsPerPage": 10,
        "defaultView": "cards",
        "defaultSort": "newest"
    }
}


# === COLLECT INPUT ===
print("\n=== Create New Tenant & Admin ===\n")
TENANT_NAME = input("Enter tenant name (e.g., 'Acme Corp'): ").strip()
TENANT_SLUG = input("Enter tenant slug (e.g., 'acme', lowercase, no spaces): ").strip().lower().replace(" ", "-")
ADMIN_EMAIL = input("Enter admin email: ").strip()
ADMIN_PASSWORD = input("Enter admin password: ").strip()

if not all([TENANT_NAME, TENANT_SLUG, ADMIN_EMAIL, ADMIN_PASSWORD]):
    print("❌ Error: All fields are required")
    exit(1)


# === LOGIC ===
session = SessionLocal()

try:
    # Check if tenant slug already exists
    existing_tenant = session.query(Tenant).filter_by(slug=TENANT_SLUG).first()
    if existing_tenant:
        print(f"❌ Error: Tenant with slug '{TENANT_SLUG}' already exists")
        exit(1)

    # Check if email already exists
    existing_user = session.query(User).filter_by(email=ADMIN_EMAIL.lower()).first()
    if existing_user:
        print(f"❌ Error: User with email '{ADMIN_EMAIL}' already exists")
        exit(1)

    # Ensure both roles exist
    admin_role = session.query(Role).filter_by(name="admin").first()
    if not admin_role:
        admin_role = Role(name="admin")
        session.add(admin_role)

    user_role = session.query(Role).filter_by(name="user").first()
    if not user_role:
        user_role = Role(name="user")
        session.add(user_role)

    session.commit()

    # Create the tenant with customized config
    tenant_config = DEFAULT_TENANT_CONFIG.copy()
    tenant_config["branding"]["companyName"] = f"{TENANT_NAME} CRM"

    new_tenant = Tenant(
        name=TENANT_NAME,
        slug=TENANT_SLUG,
        is_active=True,
        config=tenant_config,
    )
    session.add(new_tenant)
    session.commit()
    session.refresh(new_tenant)

    # Create the admin user linked to the tenant
    new_admin = User(
        email=ADMIN_EMAIL.lower(),
        password_hash=hash_password(ADMIN_PASSWORD),
        tenant_id=new_tenant.id,
        is_active=True,
        roles=[admin_role],
    )
    session.add(new_admin)
    session.commit()

    print(f"\n✅ Created new tenant and admin:")
    print(f"   Tenant: {TENANT_NAME} (slug: {TENANT_SLUG})")
    print(f"   Tenant ID: {new_tenant.id}")
    print(f"   Admin Email: {ADMIN_EMAIL}")
    print(f"   Admin Password: {ADMIN_PASSWORD}")
    print(f"\n   The admin can now log in and the frontend will receive")
    print(f"   the tenant config automatically.\n")

except Exception as e:
    session.rollback()
    print("❌ Error:", e)
finally:
    session.close()
