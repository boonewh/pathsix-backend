from app.models import User, Role
from app.database import SessionLocal
from app.utils.auth_utils import hash_password
from sqlalchemy import func

# === CONFIG ===
ADMIN_EMAIL = input("Enter admin email: ").strip()
ADMIN_PASSWORD = input("Enter admin password: ").strip()

# === LOGIC ===
session = SessionLocal()

try:
    # Get next available tenant_id
    max_tenant = session.query(func.max(User.tenant_id)).scalar()
    next_tenant_id = (max_tenant or 0) + 1

    # Ensure both roles exist
    admin_role = session.query(Role).filter_by(name="admin").first()
    if not admin_role:
        admin_role = Role(name="admin")
        session.add(admin_role)

    user_role = session.query(Role).filter_by(name="user").first()
    if not user_role:
        user_role = Role(name="user")
        session.add(user_role)

    session.commit()  # ✅ commit both roles before using

    # Create the admin user
    new_admin = User(
        email=ADMIN_EMAIL,
        password_hash=hash_password(ADMIN_PASSWORD),
        tenant_id=next_tenant_id,
        is_active=True,
        roles=[admin_role],
    )
    session.add(new_admin)
    session.commit()

    print(f"\n✅ Created new tenant admin:")
    print(f"   Email: {ADMIN_EMAIL}")
    print(f"   Password: {ADMIN_PASSWORD}")
    print(f"   Tenant ID: {next_tenant_id}\n")

except Exception as e:
    session.rollback()
    print("❌ Error:", e)
finally:
    session.close()
