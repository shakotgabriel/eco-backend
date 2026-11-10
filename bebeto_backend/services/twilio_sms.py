"""
Twilio SMS service for EcoBus.

Sends transactional SMS (booking confirmations, cancellations, ticket details,
trip reminders) via the Twilio REST API.
"""
import logging

from django.conf import settings
from twilio.rest import Client

logger = logging.getLogger(__name__)

_client = None


def _get_client():
    """Lazily initialise the Twilio REST client."""
    global _client
    if _client is None:
        _client = Client(
            settings.TWILIO_ACCOUNT_SID,
            settings.TWILIO_AUTH_TOKEN,
        )
    return _client


# ─── SMS helpers ────────────────────────────────────────────────────────────


def send_sms(phone_number, message):
    """
    Send an SMS to a single phone number via Twilio.

    Args:
        phone_number: Recipient in any common format (0722…, +254…, 254…).
        message: Text body (≤1600 chars recommended).

    Returns:
        The Twilio MessageInstance on success.

    Raises:
        twilio.base.exceptions.TwilioRestException on API error.
    """
    client = _get_client()
    to = _format_phone(phone_number)

    try:
        msg = client.messages.create(
            body=message,
            from_=settings.TWILIO_PHONE_NUMBER,
            to=to,
        )
        logger.info(f"SMS sent to {to}: sid={msg.sid}, status={msg.status}")
        return msg
    except Exception as exc:
        logger.error(f"SMS failed to {to}: {exc}")
        raise


def send_whatsapp(phone_number, message):
    """
    Send a WhatsApp message via Twilio (requires WhatsApp-enabled sender).

    Args:
        phone_number: Recipient in any common format.
        message: Text body.
    """
    client = _get_client()
    to = _format_phone(phone_number)

    try:
        msg = client.messages.create(
            body=message,
            from_=f"whatsapp:{settings.TWILIO_WHATSAPP_NUMBER}",
            to=f"whatsapp:{to}",
        )
        logger.info(f"WhatsApp sent to {to}: sid={msg.sid}")
        return msg
    except Exception as exc:
        logger.error(f"WhatsApp failed to {to}: {exc}")
        raise


def is_configured(channel="sms"):
    if not (settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN):
        return False
    if channel == "whatsapp":
        return bool(getattr(settings, "TWILIO_WHATSAPP_NUMBER", ""))
    return bool(settings.TWILIO_PHONE_NUMBER)


def notify_customer(phone_number, message):
    """
    Send a customer message on every channel in CUSTOMER_NOTIFY_CHANNELS
    ("sms", "whatsapp"). Channels without Twilio credentials are skipped so
    unconfigured environments don't retry forever.

    Returns the number of messages sent. Raises if every attempted send failed.
    """
    if not phone_number:
        return 0
    channels = [c.strip().lower() for c in getattr(settings, "CUSTOMER_NOTIFY_CHANNELS", ["sms"]) if c.strip()]
    senders = {"sms": send_sms, "whatsapp": send_whatsapp}
    sent, errors = 0, []
    for channel in channels:
        sender = senders.get(channel)
        if sender is None or not is_configured(channel):
            logger.info(f"Skipping {channel} notification: not configured")
            continue
        try:
            sender(phone_number, message)
            sent += 1
        except Exception as exc:
            errors.append(exc)
    if errors and not sent:
        raise errors[0]
    return sent


# ─── Utilities ──────────────────────────────────────────────────────────────


def _format_phone(phone_number):
    """
    Normalize phone numbers to E.164 international format.
    Handles common East African formats:
      0722000000  → +211722000000  (South Sudan default)
      +254722...  → +254722...     (already international)
      254722...   → +254722...
    """
    phone = str(phone_number).strip()
    if phone.startswith("+"):
        return phone
    if phone.startswith("0"):
        # Default to South Sudan country code (+211)
        return f"+211{phone[1:]}"
    return f"+{phone}"
