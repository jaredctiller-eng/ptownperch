import logging
import smtplib
from email.message import EmailMessage

from app.config import settings

logger = logging.getLogger(__name__)


class EmailSendError(Exception):
    pass


def send_email(*, to: str, subject: str, body: str) -> None:
    if not (settings.gmail_address and settings.gmail_app_password):
        raise EmailSendError("gmail not configured")

    message = EmailMessage()
    message["From"] = settings.gmail_address
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as smtp:
            smtp.starttls()
            smtp.login(settings.gmail_address, settings.gmail_app_password)
            smtp.send_message(message)
    except smtplib.SMTPException as exc:
        logger.error("gmail send failed: %s", exc.__class__.__name__)
        raise EmailSendError(f"gmail send failed: {exc.__class__.__name__}") from None
