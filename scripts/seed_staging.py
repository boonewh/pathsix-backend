"""
Seed the STAGING database with synthetic data. Safe to run repeatedly (idempotent).

Creates: roles, one demo tenant, an admin user, and a few sample clients/leads so the
staging CRM is usable for testing. This is fake data — NEVER run against production.

Run on staging:
  flyctl ssh console --app pathsixsolutions-backend-staging \
    -C "/venv/bin/python scripts/seed_staging.py"
"""
import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from app.database import SessionLocal
from app.models import Role, Tenant, User, Client, Lead
from app.utils.auth_utils import hash_password

# --- Staging login (throwaway; fake data only) ------------------------------
TENANT_NAME = "Staging Demo Co"
TENANT_SLUG = "staging"
ADMIN_EMAIL = "admin@staging.test"
ADMIN_PASSWORD = "StagingDemo!2026"

# Full tenant config (frontend needs businessTypes + leads.statuses/sources or it crashes)
TENANT_CONFIG = {
    "branding": {
        "companyName": "Staging Demo Co",
        "primaryColor": "#2563eb",
        "secondaryColor": "#64748b",
        "logo": None,
        "logoCompact": None,
    },
    "labels": {"client": "Client", "lead": "Lead", "project": "Project", "interaction": "Interaction"},
    "leads": {
        "statuses": ["new", "contacted", "qualified", "lost", "converted"],
        "statusConfig": {
            "colors": {
                "new": "bg-yellow-100 text-yellow-800",
                "contacted": "bg-blue-100 text-blue-800",
                "qualified": "bg-orange-100 text-orange-800",
                "lost": "bg-red-100 text-red-800",
                "converted": "bg-green-100 text-green-800",
            },
            "labels": {
                "new": "New", "contacted": "Contacted", "qualified": "Qualified",
                "lost": "Lost", "converted": "Converted",
            },
        },
        "sources": ["Website", "Referral", "Cold Call", "Email Campaign",
                    "Social Media", "Trade Show", "Advertisement", "Partner", "Other"],
        "temperatures": ["hot", "warm", "cold"],
        "temperatureConfig": {"colors": {"hot": "text-red-600", "warm": "text-orange-600", "cold": "text-blue-600"}},
    },
    "businessTypes": ["None", "Professional Services", "Technology", "Manufacturing",
                      "Retail", "Healthcare", "Finance", "Education", "Other"],
    "regional": {"currency": "USD", "currencySymbol": "$", "dateFormat": "MM/DD/YYYY",
                 "phoneFormat": "US", "addressFormat": "US"},
    "features": {"showTemperature": True, "showLeadScore": False, "showSource": True,
                 "enableProjectStandalone": True, "enableBulkOperations": True,
                 "enableAdvancedFilters": True, "enableDataExport": True},
    "defaults": {"leadsPerPage": 10, "clientsPerPage": 10, "projectsPerPage": 10,
                 "defaultView": "cards", "defaultSort": "newest"},
}


def main():
    session = SessionLocal()
    try:
        # Roles
        roles = {}
        for name in ("admin", "user", "file_uploads"):
            role = session.query(Role).filter_by(name=name).first()
            if not role:
                role = Role(name=name)
                session.add(role)
            roles[name] = role
        session.commit()

        # Tenant
        tenant = session.query(Tenant).filter_by(slug=TENANT_SLUG).first()
        if not tenant:
            tenant = Tenant(name=TENANT_NAME, slug=TENANT_SLUG, is_active=True, config=TENANT_CONFIG)
            session.add(tenant)
            session.commit()
            session.refresh(tenant)

        # Admin user
        admin = session.query(User).filter_by(email=ADMIN_EMAIL).first()
        if not admin:
            admin = User(
                email=ADMIN_EMAIL,
                password_hash=hash_password(ADMIN_PASSWORD),
                tenant_id=tenant.id,
                is_active=True,
                roles=[roles["admin"]],
            )
            session.add(admin)
            session.commit()
            session.refresh(admin)

        # Sample clients / leads (non-fatal if the schema shifts)
        try:
            if not session.query(Client).filter_by(tenant_id=tenant.id).first():
                session.add_all([
                    Client(tenant_id=tenant.id, created_by=admin.id, name="Acme Industrial",
                           contact_person="Dana Ruiz", email="dana@acme.test", phone="5125550101",
                           city="Austin", state="TX", status="active", type="Manufacturing"),
                    Client(tenant_id=tenant.id, created_by=admin.id, name="Bluebonnet Retail",
                           contact_person="Sam Lee", email="sam@bluebonnet.test", phone="2145550102",
                           city="Dallas", state="TX", status="active", type="Retail"),
                ])
            if not session.query(Lead).filter_by(tenant_id=tenant.id).first():
                session.add_all([
                    Lead(tenant_id=tenant.id, created_by=admin.id, name="Cypress Health",
                         email="ops@cypress.test", phone="7135550103", lead_status="new", lead_source="Website"),
                    Lead(tenant_id=tenant.id, created_by=admin.id, name="Delta Logistics",
                         email="hi@delta.test", phone="8325550104", lead_status="contacted", lead_source="Referral"),
                ])
            session.commit()
        except Exception as e:
            session.rollback()
            print(f"(sample data skipped: {e})")

        print("Seed complete.")
        print(f"  Tenant: {tenant.name} (slug={tenant.slug}, id={tenant.id})")
        print(f"  Login:  {ADMIN_EMAIL} / {ADMIN_PASSWORD}")
    except Exception as e:
        session.rollback()
        print("ERROR:", e)
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
