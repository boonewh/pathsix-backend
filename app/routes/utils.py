from quart import Blueprint, request
import logging

utils_bp = Blueprint("utils", __name__, url_prefix="/api")

@utils_bp.route("/log-error", methods=["POST"])
async def log_error():
    data = await request.get_json()
    message = data.get("message", "No message provided")
    context = data.get("context", {})

    logging.error(f"[Frontend Error] {message} | Context: {context}")
    return {"status": "logged"}
