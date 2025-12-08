from quart import Quart, request
from quart_cors import cors
from app.routes import register_blueprints
from app.utils.keep_alive import keep_db_alive  # ✅ this still works
from app.database import SessionLocal
from sqlalchemy import text
import asyncio
import sentry_sdk
from sentry_sdk.integrations.quart import QuartIntegration
from app.utils.logging_utils import logger, log_endpoint
import time

# 👇 Add warmup function directly here
async def warmup_db():
    retries = 5
    delay = 2
    while retries > 0:
        try:
            session = SessionLocal()
            session.execute(text("SELECT 1"))  # ✅ Wrap in text()
            session.close()
            print("[Warmup] Postgres is ready.")
            return
        except Exception as e:
            print(f"[Warmup] Waiting for DB... ({retries} left) {e}")
            await asyncio.sleep(delay)
            retries -= 1
    print("[Warmup] Gave up waiting for DB.")

def create_app():
    app = Quart(__name__)

    # ✅ Add CORS *before* anything else
    app = cors(
        app,
        allow_origin=["https://pathsix-crm.vercel.app", "https://test-crm-six.vercel.app", "https://pathsixdesigns-crm.vercel.app", "http://localhost:5173", "http://localhost:5174", "http://localhost:5175"],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],          # ← add this
        expose_headers=["Content-Disposition"]
    )

    app.config.from_pyfile("config.py")
    app.config.setdefault("STORAGE_ROOT", "./storage")
    app.config.setdefault("MAX_CONTENT_LENGTH", 20 * 1024 * 1024)  # 20 MB
    app.config.setdefault("STORAGE_VENDOR", "disk")  # "disk" | "b2"

    # Initialize Sentry
    if app.config.get("SENTRY_DSN"):
        sentry_sdk.init(
            dsn=app.config["SENTRY_DSN"],
            integrations=[
                QuartIntegration(),
            ],
            # Set traces_sample_rate to 1.0 to capture 100%
            # of transactions for performance monitoring.
            traces_sample_rate=1.0,
            # Set profiles_sample_rate to 1.0 to profile 100%
            # of sampled transactions.
            profiles_sample_rate=1.0,
        )

    register_blueprints(app)

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

    #✅ Before serving: warm up DB, then start keep-alive
    @app.before_serving
    async def startup():
        await warmup_db()
        app.add_background_task(keep_db_alive)
        logger.info("PathSix CRM backend started successfully")

    return app
