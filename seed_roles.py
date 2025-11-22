# scripts/seed_roles.py
import os, sys
from contextlib import contextmanager

# Allow "from app import ..." when running this script directly
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from app.database import SessionLocal  # uses your configured DB
from app.models import Role            # Role has unique name

@contextmanager
def session_scope():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except:
        session.rollback()
        raise
    finally:
        session.close()

def ensure_role(session, name: str):
    role = session.query(Role).filter_by(name=name).first()
    if not role:
        session.add(Role(name=name))
        return True
    return False

if __name__ == "__main__":
    with session_scope() as s:
        created = []
        for r in ("admin", "user", "file_uploads"):
            if ensure_role(s, r):
                created.append(r)
        if created:
            print("Created roles:", ", ".join(created))
        else:
            print("All roles already present.")
