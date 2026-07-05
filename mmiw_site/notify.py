from __future__ import annotations
import json
import urllib.request
import urllib.error
from .settings import settings


class DeliveryResult:
    def __init__(self, ok: bool, error: str | None = None):
        self.ok = ok
        self.error = error


def send_email(to_address: str, subject: str, body: str) -> DeliveryResult:
    """Sends email via Resend's HTTPS API instead of raw SMTP.

    This exists because most free-tier hosting platforms (Render, Railway,
    and others) block outbound SMTP (ports 25/465/587) to prevent spam abuse
    — this is a documented, deliberate infrastructure policy, not something
    specific to this app. Resend sends over plain HTTPS, which isn't
    blocked, and has a genuinely permanent free tier (3,000 emails/month).

    settings.resend_from_address defaults to Resend's own shared test
    domain (onboarding@resend.dev), which requires no setup — real
    deployments should eventually verify their own sending domain with
    Resend for better deliverability, but this works correctly as-is.
    """
    if not settings.resend_api_key:
        return DeliveryResult(False, "email_not_configured")
    try:
        payload = json.dumps({
            "from": settings.resend_from_address,
            "to": [to_address],
            "subject": subject,
            "text": body,
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://api.resend.com/emails",
            data=payload,
            headers={
                "Authorization": f"Bearer {settings.resend_api_key}",
                "Content-Type": "application/json",
                # Cloudflare (which fronts Resend's API) blocks requests with
                # no User-Agent or a bot-like one (Cloudflare error 1010).
                # A normal-looking UA avoids that false-positive block.
                "User-Agent": "MMIW-Panic/1.0 (+https://mmiw-panic.onrender.com)",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
        return DeliveryResult(True)
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8")
        except Exception:
            detail = str(e)
        return DeliveryResult(False, f"resend_http_error_{e.code}: {detail}")
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

