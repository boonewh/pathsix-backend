"""
================================================================================
CREATE NEW TENANT WITH CUSTOM CONFIGURATION
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
# Step 0: EDIT THIS FILE to set TENANT_NAME, TENANT_SLUG, and TENANT_CONFIG before #            
#         running. After editing, flyctl deploy. THEN SSH in.
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
#         # /venv/bin/python create_custom_tenant.py
#
# Step 5: Follow the prompts (confirm tenant, enter admin email/password)
#
# Step 6: Exit SSH when done
#         # exit
#
# ══════════════════════════════════════════════════════════════════════════════
# OPTION 2: DATABASE PROXY (Run locally against production DB)
# ══════════════════════════════════════════════════════════════════════════════
#
# Step 0: EDIT THIS FILE to set TENANT_NAME, TENANT_SLUG, and TENANT_CONFIG before #            
#         running. After editing, flyctl deploy. THEN proxy in.
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
#         $ python create_custom_tenant.py
#
# Step 4: Follow the prompts (confirm tenant, enter admin email/password)
#
# Step 5: Close the proxy in terminal 1 (Ctrl+C)
#
# NOTE: To find your DB password, check Fly.io secrets:
#       $ flyctl secrets list -a pathsixsolutions-backend
#       The DATABASE_URL secret contains the password.
#
################################################################################

WHAT THIS SCRIPT DOES:
    1. Creates a TENANT record in the database with a CUSTOM configuration
    2. Prompts you to create an ADMIN USER for that tenant
    All in one step - edit config, run script, done!

--------------------------------------------------------------------------------
FAQ - READ THIS BEFORE RUNNING
--------------------------------------------------------------------------------

Q: What is a "tenant"?
A: A tenant is an organization that uses your CRM. Example: "ASFI" is tenant #1.
   Each tenant has their own branding, labels, lead statuses, etc.

Q: What is a "user"?
A: A user is a person who logs into the CRM. Users belong to a tenant.
   Example: boonewh@pathsixdesigns.com is a USER who belongs to ASFI (tenant).

Q: Do I edit this file every time I want a new tenant?
A: YES. Edit TENANT_NAME, TENANT_SLUG, and TENANT_CONFIG below, then run the script.
   Once you run it, the config is saved to the DATABASE permanently.
   Then you can edit this file again for the next tenant - previous data is safe.

Q: Where is the config stored after I run this?
A: In the DATABASE, in the "tenants" table, in the "config" column (as JSON).
   The frontend does NOT store it - it receives it from the backend on login.

Q: Can multiple users share the same tenant config?
A: YES! That's the whole point. One tenant = one organization. BUT, they will also share the 
    same branding, labels, and settings.
   All users with the same tenant_id share the same config (branding, labels, etc.)

Q: What if two tenants want the same labels but different branding?
A: Create two separate tenants anyway. Each tenant needs their own record because:
   - They need different branding (company name, colors)
   - They need different tenant IDs (keeps their data separate)
   - They might customize things differently later

Q: When do I use this script vs create_default_tenant.py?
A: - THIS SCRIPT (create_custom_tenant.py): When you need CUSTOM configuration (edit the config below)
   - create_default_tenant.py: Quick setup with DEFAULT config (no editing required)

--------------------------------------------------------------------------------
HOW TO USE
--------------------------------------------------------------------------------

1. Edit TENANT_NAME and TENANT_SLUG below
2. Edit TENANT_CONFIG to customize their settings
3. Run: python create_custom_tenant.py
4. Confirm with 'y'
5. Enter admin email and password when prompted
6. Done! The admin can now log in with the custom config.

================================================================================
"""

import os
import sys
import json

# Add the app to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.database import SessionLocal
from app.models import Tenant, User, Role
from app.utils.auth_utils import hash_password

# =============================================================================
# EDIT THESE VALUES FOR YOUR NEW TENANT
# =============================================================================

TENANT_NAME = "PathSix Solutions"  # Display name
TENANT_SLUG = "pathsixsolutions"             # URL-safe identifier (lowercase, no spaces)

TENANT_CONFIG = {
    "branding": {
        "companyName": "PathSix Solutions CRM",
        "primaryColor": "#05133D",      # Main brand color
        "secondaryColor": "#F59E0B",    # Secondary color
        # Logo URLs - leave empty/null to use default PathSix logos
        # Put custom logos in frontend/public/logos/ folder
        # Example: "/logos/acme-logo.png" or full URL "https://cdn.example.com/logo.png"
        "logo": None,                   # Full logo (e.g., "/logos/tenant-logo.png")
        "logoCompact": None,            # Compact/icon logo (e.g., "/logos/tenant-icon.png")
    },
    "labels": {
        "client": "Client",             # What to call clients
        "lead": "Lead",                 # What to call leads
        "project": "Project",           # What to call projects
        "interaction": "Interaction",   # What to call interactions
    },
    "leads": {
        "statuses": ["open", "qualified", "proposal", "won", "lost"],
        "statusConfig": {
            "colors": {
                "open": "bg-yellow-100 text-yellow-800",
                "qualified": "bg-orange-100 text-orange-800",
                "proposal": "bg-blue-100 text-blue-800",
                "won": "bg-green-100 text-green-800",
                "lost": "bg-red-100 text-red-800"
            },
            "icons": {
                "open": "circle-yellow",
                "qualified": "circle-orange",
                "proposal": "circle-blue",
                "won": "circle-green",
                "lost": "circle-red"
            },
            "labels": {
                "open": "Open",
                "qualified": "Qualified",
                "proposal": "Proposal",
                "won": "Won",
                "lost": "Lost"
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

# =============================================================================
# SCRIPT - DON'T EDIT BELOW THIS LINE
# =============================================================================

def create_tenant_and_admin():
    print(f"\nCreating tenant: {TENANT_NAME} (slug: {TENANT_SLUG})")
    print("-" * 50)

    with SessionLocal() as db:
        # Check if slug already exists
        existing = db.query(Tenant).filter(Tenant.slug == TENANT_SLUG).first()
        if existing:
            print(f"ERROR: Tenant with slug '{TENANT_SLUG}' already exists (id={existing.id})")
            return False

        # Create the tenant
        tenant = Tenant(
            name=TENANT_NAME,
            slug=TENANT_SLUG,
            is_active=True,
            config=TENANT_CONFIG
        )
        db.add(tenant)
        db.commit()
        db.refresh(tenant)

        print(f"SUCCESS! Created tenant:")
        print(f"  ID:   {tenant.id}")
        print(f"  Name: {tenant.name}")
        print(f"  Slug: {tenant.slug}")

        # Now create admin user
        print("\n" + "-" * 50)
        print("CREATE ADMIN USER FOR THIS TENANT")
        print("-" * 50)

        admin_email = input("Enter admin email: ").strip().lower()
        admin_password = input("Enter admin password: ").strip()

        if not admin_email or not admin_password:
            print("ERROR: Email and password are required")
            return False

        # Check if email already exists
        existing_user = db.query(User).filter(User.email == admin_email).first()
        if existing_user:
            print(f"ERROR: User with email '{admin_email}' already exists")
            return False

        # Ensure admin role exists
        admin_role = db.query(Role).filter(Role.name == "admin").first()
        if not admin_role:
            admin_role = Role(name="admin")
            db.add(admin_role)
            db.commit()

        # Create the admin user
        new_admin = User(
            email=admin_email,
            password_hash=hash_password(admin_password),
            tenant_id=tenant.id,
            is_active=True,
            roles=[admin_role]
        )
        db.add(new_admin)
        db.commit()

        print(f"\nSUCCESS! Created admin user:")
        print(f"  Email:    {admin_email}")
        print(f"  Password: {admin_password}")
        print(f"  Tenant:   {TENANT_NAME} (id={tenant.id})")
        print(f"\nThe admin can now log in and will see the custom config!")

        return True


if __name__ == "__main__":
    print("\n" + "=" * 50)
    print("CREATE NEW TENANT WITH CUSTOM CONFIG")
    print("=" * 50)

    # Confirmation
    print(f"\nAbout to create tenant:")
    print(f"  Name: {TENANT_NAME}")
    print(f"  Slug: {TENANT_SLUG}")
    print(f"  Config: {len(json.dumps(TENANT_CONFIG))} bytes of JSON")

    response = input("\nProceed? (y/n): ").strip().lower()
    if response == 'y':
        create_tenant_and_admin()
    else:
        print("Cancelled.")
