from __future__ import annotations
import smtplib
from email.message import EmailMessage
from .settings import settings


class DeliveryResult:
    def __init__(self, ok: bool, error: str | None = None):
        self.ok = ok
        self.error = error


def send_email(to_address: str, subject: str, body: str) -> DeliveryResult:
    if not (settings.smtp_user and settings.smtp_pass and settings.smtp_host):
        return DeliveryResult(False, "email_not_configured")
    try:
        msg = EmailMessage()
        msg["From"] = settings.smtp_user
        msg["To"] = to_address
        msg["Subject"] = subject
        msg.set_content(body)
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as s:
            s.starttls()
            s.login(settings.smtp_user, settings.smtp_pass)
            s.send_message(msg)
        return DeliveryResult(True)
    except Exception as e:
        return DeliveryResult(False, str(e))


def send_sms(to_number: str, body: str) -> DeliveryResult:
    if not (settings.twilio_sid and settings.twilio_token and settings.twilio_from):
        return DeliveryResult(False, "sms_not_configured")
    try:
        # Imported lazily so the app runs fine (email-only) without the
        # twilio package installed at all.
        from twilio.rest import Client
        client = Client(settings.twilio_sid, settings.twilio_token)
        client.messages.create(to=to_number, from_=settings.twilio_from, body=body)
        return DeliveryResult(True)
    except Exception as e:
        return DeliveryResult(False, str(e))


def dispatch(contact: dict, subject: str, body: str) -> DeliveryResult:
    """Routes a single contact to the right channel based on contact_type."""
    ctype = contact.get("contact_type")
    dest = contact.get("destination")
    if ctype == "email":
        return send_email(dest, subject, body)
    if ctype == "sms":
        return send_sms(dest, body)
    return DeliveryResult(False, f"unknown_contact_type:{ctype}")
