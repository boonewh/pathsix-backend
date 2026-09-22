from quart import Quart, request
from quart_cors import cors
from app.routes import register_blueprints
from app.database import SessionLocal
from sqlalchemy import text
import asyncio
import sentry_sdk
from sentry_sdk.integrations.quart import QuartIntegration
from app.utils.logging_utils import logger, log_endpoint
import time
import os

# 👇 Add warmup function directly here
async def warmup_db():
    retries = 5
    delay = 2
    while retries > 0:
        try:
            with SessionLocal() as session:
                session.execute(text("SELECT 1"))
            print("[Warmup] Postgres is ready.")
            return
        except Exception as e:
            print(f"[Warmup] Waiting for DB... ({retries} left) {e}")
            await asyncio.sleep(delay)
            retries -= 1
    print("[Warmup] Gave up waiting for DB.")

def create_app():
    from app.utils.sales_audit import register_sales_audit
    register_sales_audit()
    app = Quart(__name__)

    # ✅ Add CORS *before* anything else.
    # Base allow-list plus any extra origins from CORS_ALLOWED_ORIGINS (CSV env var),
    # e.g. the staging frontend origin — set as a Fly secret on staging only.
    _default_origins = ["https://pathsix-crm.vercel.app", "https://test-crm-six.vercel.app", "https://pathsixdesigns-crm.vercel.app", "http://localhost:5173", "http://localhost:5174", "http://localhost:5175"]
    _extra_origins = [o.strip() for o in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()]
    app = cors(
        app,
        allow_origin=_default_origins + _extra_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],          # ← add this
        expose_headers=["Content-Disposition"]
    )

    app.config.from_pyfile("config.py")
    app.config.setdefault("STORAGE_ROOT", "./storage")
    app.config.setdefault("MAX_CONTENT_LENGTH", 20 * 1024 * 1024)  # 20 MB
    app.config.setdefault("STORAGE_VENDOR", "disk")  # "disk" | "b2"

    # Extended timeout for long-running operations (backups, restores)
    # Quart uses hypercorn under the hood - set keep_alive_timeout
    app.config.setdefault("RESPONSE_TIMEOUT", 600)  # 10 minutes for backup/restore operations

    # Initialize Sentry
    if app.config.get("SENTRY_DSN"):
        sentry_sdk.init(
            dsn=app.config["SENTRY_DSN"],
            integrations=[
                QuartIntegration(),
            ],
            # Sample performance without collecting request bodies or default PII.
            send_default_pii=False,
            max_request_body_size="never",
            traces_sample_rate=0.1,

            profiles_sample_rate=0.0,
        )

    register_blueprints(app)

    # Lightweight liveness endpoint for the Fly deploy health check (no auth, no DB).
    @app.route("/api/health")
    async def health():
        return {"status": "ok"}, 200

    # Request logging middleware
    @app.before_request
    async def before_request():
        request.start_time = time.time()
    
    @app.after_request
    async def after_request(response):
        if hasattr(request, 'start_time'):
            duration_ms = (time.time() - request.start_time) * 1000
            log_endpoint(
                endpoint_name=request.endpoint or request.path,
                duration_ms=duration_ms,
                status_code=response.status_code
            )
        return response

    # Before serving: warm up the DB. (keep-alive loop removed — pool_pre_ping in
    # database.py handles stale connections, and the old loop poisoned the scoped
    # session on failure, causing the "invalid transaction" errors seen in prod logs.)
    @app.before_serving
    async def startup():
        await warmup_db()
        logger.info("PathSix CRM backend started successfully")

    return app
