# app/utils/email_utils.py
import aiosmtplib
from email.message import EmailMessage
from app.config import (
    MAIL_SERVER,
    MAIL_PORT,
    MAIL_USERNAME,
    MAIL_PASSWORD,
    MAIL_USE_TLS,
    MAIL_FROM_NAME,
    MAIL_FROM_EMAIL,
)

async def send_email(subject: str, recipient: str, body: str):
    message = EmailMessage()
    message["From"] = f"{MAIL_FROM_NAME} <{MAIL_FROM_EMAIL}>"
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)

    await aiosmtplib.send(
        message,
        hostname=MAIL_SERVER,
        port=MAIL_PORT,
        username=MAIL_USERNAME,
        password=MAIL_PASSWORD,
        start_tls=MAIL_USE_TLS,
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
        # Optional: log the error so it's not silent
        print(f"Failed to send assignment notification: {e}")