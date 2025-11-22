from app import create_app
import os

app = create_app()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    # Quart dev server — ok for now; switch to Hypercorn/Uvicorn for prod later
    app.run(host="0.0.0.0", port=port, use_reloader=False)