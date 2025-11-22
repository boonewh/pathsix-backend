import asyncio
from sqlalchemy import text
from app.database import SessionLocal

async def keep_db_alive():
    while True:
        try:
            session = SessionLocal()
            session.execute(text("SELECT 1"))  # ✅ required by SQLAlchemy 2+
            session.close()
            print("[KeepAlive] DB pinged successfully.")
        except Exception as e:
            print(f"[KeepAlive] DB ping failed: {e}")
        await asyncio.sleep(300)  # every 5 minutes
