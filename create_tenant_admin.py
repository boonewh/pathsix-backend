from app.models import User, Role, Tenant
from app.database import SessionLocal
from app.utils.auth_utils import hash_password

# === DEFAULT CONFIG TEMPLATE ===
# New tenants get this default config - can be customized later via admin panel
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
