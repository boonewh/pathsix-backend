# app/utils/email_utils.py
import asyncio
import json
import logging
import ssl
import urllib.error
import urllib.request

import certifi
from quart import current_app
from app.config import MAIL_FROM_NAME, MAIL_FROM_EMAIL, RESEND_API_KEY
from app.utils.auth_utils import generate_reset_token

RESEND_URL = "https://api.resend.com/emails"
log = logging.getLogger(__name__)


class EmailDeliveryError(RuntimeError):
    """Resend refused or could not be reached; the message holds no secrets."""


def _post_to_resend(payload: dict) -> str:
    request = urllib.request.Request(
        RESEND_URL,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": "pathsix-crm",
        },
    )
    context = ssl.create_default_context(cafile=certifi.where())
    try:
        with urllib.request.urlopen(request, timeout=15, context=context) as response:
            return json.loads(response.read() or b"{}").get("id", "")
    except urllib.error.HTTPError as error:
        # Resend explains refusals (unverified domain, bad key) in the body.
        detail = error.read(300).decode(errors="replace")
        raise EmailDeliveryError(f"Resend refused the email ({error.code}): {detail}") from None
    except (urllib.error.URLError, TimeoutError) as error:
        raise EmailDeliveryError(f"Resend could not be reached ({type(error).__name__})") from None


async def send_email(subject: str, recipient: str, body: str):
    """Send a plain-text email through Resend's HTTP API."""
    if not RESEND_API_KEY:
        raise EmailDeliveryError("RESEND_API_KEY is not set")
    payload = {
        "from": f"{MAIL_FROM_NAME} <{MAIL_FROM_EMAIL}>",
        "to": [recipient],
        "subject": subject,
        "text": body,
    }
    try:
        return await asyncio.to_thread(_post_to_resend, payload)
    except EmailDeliveryError as error:
        log.warning("Email delivery failed: %s", error)
        raise


async def send_password_reset_email(recipient: str):
    """Create and deliver a password-reset link for an existing user."""
    token = generate_reset_token(recipient)
    reset_link = f"{current_app.config['FRONTEND_URL']}/reset-password/{token}"

    await send_email(
        subject="Password Reset Request",
        recipient=recipient,
        body=f"Click to reset your password: {reset_link}",
    )

async def send_assignment_notification(to_email: str, entity_type: str, entity_name: str, assigned_by: str):
    subject = f"New {entity_type.capitalize()} Assigned to You"
    body = (
        f"You've been assigned to the {entity_type} '{entity_name}' by {assigned_by}.\n"
        f"Please log in to the CRM to view the details.\n\n"
        f"— {MAIL_FROM_NAME}"
    )
    try:
        await send_email(subject, to_email, body)
    except Exception as e:
        log.warning("Failed to send assignment notification (%s)", type(e).__name__)
