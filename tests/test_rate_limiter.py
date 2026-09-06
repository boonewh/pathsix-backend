import asyncio

from quart import Quart

from app.utils.rate_limiter import rate_limit, reset_rate_limit


def test_rate_limits_are_independent_per_endpoint():
    reset_rate_limit()
    app = Quart(__name__)

    @app.get("/login-like")
    @rate_limit(max_attempts=1, window_seconds=60)
    async def login_like():
        return {"status": "ok"}

    @app.get("/reset-like")
    @rate_limit(max_attempts=1, window_seconds=60)
    async def reset_like():
        return {"status": "ok"}

    async def exercise():
        client = app.test_client()

        first_login = await client.get("/login-like")
        first_reset = await client.get("/reset-like")
        second_login = await client.get("/login-like")

        assert first_login.status_code == 200
        assert first_reset.status_code == 200
        assert second_login.status_code == 429

    asyncio.run(exercise())


def test_forwarded_headers_cannot_reset_limit():
    reset_rate_limit()
    app = Quart(__name__)

    @app.get('/limited')
    @rate_limit(max_attempts=1, window_seconds=60)
    async def limited():
        return {'ok': True}

    async def exercise():
        client = app.test_client()
        assert (await client.get('/limited', headers={'X-Forwarded-For': '1.2.3.4'})).status_code == 200
        assert (await client.get('/limited', headers={'X-Forwarded-For': '5.6.7.8', 'X-Real-IP': '5.6.7.8'})).status_code == 429

    asyncio.run(exercise())
