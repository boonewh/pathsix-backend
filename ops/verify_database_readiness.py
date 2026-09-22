"""Run inside the production app: read-only checks, no real user credentials."""
import asyncio
import time

from authlib.jose import jwt
from sqlalchemy import text
from app import create_app
from app.database import SessionLocal


async def main():
    app = create_app()
    with SessionLocal() as session:
        assert session.execute(text("SELECT 1")).scalar() == 1
    # An impossible user ID exercises real authentication DB lookups without
    # impersonating a user or invoking the business-data route handlers.
    token = jwt.encode(
        {"alg": "HS256"},
        {"sub": -1, "roles": [], "exp": int(time.time()) + 60},
        app.config["SECRET_KEY"],
    ).decode()
    client = app.test_client()

    async def check(path):
        response = await client.get(path, headers={"Authorization": "Bearer " + token})
        body = await response.get_json()
        assert response.status_code == 401 and body == {"error": "User not found"}, path
        print(path, "database lookup passed; nonexistent user rejected")

    await asyncio.gather(*(check(path) for path in (
        "/api/clients/", "/api/leads/", "/api/projects/",
    )))
    print("READ_ONLY_DATABASE_SMOKE_OK")


asyncio.run(main())
