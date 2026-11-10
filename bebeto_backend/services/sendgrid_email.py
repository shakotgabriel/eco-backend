"""
SendGrid email service for EcoBus.

Sends transactional emails (booking confirmations, cancellations) with
branded HTML templates and optional PDF ticket attachments via SendGrid API.
"""
import base64
import logging

from django.conf import settings
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import (
    Attachment,
    Disposition,
    FileContent,
    FileName,
    FileType,
    Mail,
    MimeType,
)

logger = logging.getLogger(__name__)

_client = None


def _get_client():
    """Lazily initialise the SendGrid API client."""
    global _client
    if _client is None:
        _client = SendGridAPIClient(api_key=settings.SENDGRID_API_KEY)
    return _client


# ─── Sending ────────────────────────────────────────────────────────────────


def send_email(to_email, subject, html_content, plain_content=None, attachments=None):
    """
    Send an email via SendGrid.

    Args:
        to_email: Recipient email address.
        subject: Email subject line.
        html_content: HTML body of the email.
        plain_content: Optional plain-text fallback.
        attachments: Optional list of dicts with keys:
            - content (bytes): raw file content
            - filename (str): e.g. "ticket.pdf"
            - type (str): MIME type, e.g. "application/pdf"

    Returns:
        SendGrid response on success.
    """
    message = Mail(
        from_email=settings.DEFAULT_FROM_EMAIL,
        to_emails=to_email,
        subject=subject,
        html_content=html_content,
        plain_text_content=plain_content,
    )

    if attachments:
        for att in attachments:
            encoded = base64.b64encode(att["content"]).decode()
            attachment = Attachment(
                FileContent(encoded),
                FileName(att["filename"]),
                FileType(att.get("type", "application/octet-stream")),
                Disposition("attachment"),
            )
            message.attachment = attachment

    try:
        client = _get_client()
        response = client.send(message)
        logger.info(
            f"Email sent to {to_email}: status={response.status_code}, "
            f"subject='{subject}'"
        )
        return response
    except Exception as exc:
        logger.error(f"SendGrid email failed to {to_email}: {exc}")
        raise


# ─── HTML Templates ─────────────────────────────────────────────────────────

BRAND_COLOR = "#bb1a28"
BRAND_NAME = "EcoBus"


def _base_template(body_content):
    """Wrap body content in the branded email layout."""
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
    </head>
    <body style="margin:0; padding:0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background-color:#f5f5f5;">
        <table width="100%" cellpadding="0" cellspacing="0" style="background-color:#f5f5f5; padding:20px 0;">
            <tr>
                <td align="center">
                    <table width="600" cellpadding="0" cellspacing="0" style="background-color:#ffffff; border-radius:8px; overflow:hidden; box-shadow:0 2px 8px rgba(0,0,0,0.1);">
                        <!-- Header -->
                        <tr>
                            <td style="background-color:{BRAND_COLOR}; padding:24px 32px; text-align:center;">
                                <h1 style="margin:0; color:#ffffff; font-size:24px; letter-spacing:1px;">{BRAND_NAME}</h1>
                                <p style="margin:4px 0 0; color:rgba(255,255,255,0.8); font-size:13px;">Safe &amp; Reliable Inter-City Travel</p>
                            </td>
                        </tr>
                        <!-- Body -->
                        <tr>
                            <td style="padding:32px;">
                                {body_content}
                            </td>
                        </tr>
                        <!-- Footer -->
                        <tr>
                            <td style="background-color:#f9f9f9; padding:20px 32px; text-align:center; border-top:1px solid #eee;">
                                <p style="margin:0; color:#888; font-size:12px;">
                                    {BRAND_NAME} &bull; East Africa's Trusted Bus Service<br>
                                    Juba &bull; Kampala &bull; Nairobi &bull; Kigali
                                </p>
                            </td>
                        </tr>
                    </table>
                </td>
            </tr>
        </table>
    </body>
    </html>
    """


def booking_confirmation_html(booking, tickets, trip):
    """Build HTML content for booking confirmation email."""
    ticket_rows = ""
    for t in tickets:
        seat_info = t.seat.seat_number if t.seat else "Open"
        ticket_rows += f"""
        <tr>
            <td style="padding:8px 12px; border-bottom:1px solid #eee;">{t.first_name} {t.last_name}</td>
            <td style="padding:8px 12px; border-bottom:1px solid #eee;">{t.ticket_number}</td>
            <td style="padding:8px 12px; border-bottom:1px solid #eee;">Seat {seat_info}</td>
            <td style="padding:8px 12px; border-bottom:1px solid #eee;">{t.seat_class}</td>
        </tr>
        """

    route = trip.route
    body = f"""
    <h2 style="margin:0 0 8px; color:{BRAND_COLOR};">Booking Confirmed ✓</h2>
    <p style="margin:0 0 24px; color:#555;">Dear {booking.payer_name},</p>
    <p style="color:#333;">Your booking has been confirmed. Here are your trip details:</p>

    <!-- Trip Details -->
    <table width="100%" cellpadding="0" cellspacing="0" style="margin:16px 0; background-color:#f8f9ff; border-radius:6px; border:1px solid #e8eaf6;">
        <tr>
            <td style="padding:16px;">
                <table width="100%" cellpadding="0" cellspacing="0">
                    <tr>
                        <td style="padding:4px 0;">
                            <strong style="color:{BRAND_COLOR};">Booking Reference</strong><br>
                            <span style="font-size:18px; font-weight:bold; color:#333;">{booking.booking_reference}</span>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding:4px 0;">
                            <strong style="color:{BRAND_COLOR};">Route</strong><br>
                            <span style="color:#333;">{route.origin.name} → {route.destination.name}</span>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding:4px 0;">
                            <strong style="color:{BRAND_COLOR};">Departure</strong><br>
                            <span style="color:#333;">{trip.departure_time.strftime('%A, %d %B %Y at %H:%M')}</span>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding:4px 0;">
                            <strong style="color:{BRAND_COLOR};">Arrival</strong><br>
                            <span style="color:#333;">{trip.arrival_time.strftime('%A, %d %B %Y at %H:%M')}</span>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding:4px 0;">
                            <strong style="color:{BRAND_COLOR};">Bus</strong><br>
                            <span style="color:#333;">{trip.bus}</span>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding:4px 0;">
                            <strong style="color:{BRAND_COLOR};">Total Paid</strong><br>
                            <span style="font-size:18px; font-weight:bold; color:#2e7d32;">{route.currency} {booking.total_price}</span>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>

    <!-- Passengers -->
    <h3 style="margin:24px 0 8px; color:{BRAND_COLOR};">Passengers</h3>
    <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #ddd; border-radius:4px;">
        <tr style="background-color:{BRAND_COLOR}; color:#fff;">
            <th style="padding:10px 12px; text-align:left; font-size:13px;">Name</th>
            <th style="padding:10px 12px; text-align:left; font-size:13px;">Ticket #</th>
            <th style="padding:10px 12px; text-align:left; font-size:13px;">Seat</th>
            <th style="padding:10px 12px; text-align:left; font-size:13px;">Class</th>
        </tr>
        {ticket_rows}
    </table>

    <div style="margin:24px 0; padding:16px; background-color:#fff3e0; border-radius:6px; border-left:4px solid #ff9800;">
        <strong style="color:#e65100;">⏰ Important</strong>
        <p style="margin:8px 0 0; color:#555;">Please arrive at the bus station at least <strong>30 minutes</strong> before departure. Carry a valid photo ID.</p>
    </div>

    <p style="color:#555;">Your PDF ticket(s) are attached to this email. You can also download them anytime from our website.</p>
    <p style="color:#555;">Thank you for choosing {BRAND_NAME}! We wish you a safe and comfortable journey.</p>
    """
    return _base_template(body)


def booking_cancellation_html(booking, trip):
    """Build HTML content for booking cancellation email."""
    route = trip.route
    body = f"""
    <h2 style="margin:0 0 8px; color:#c62828;">Booking Cancelled</h2>
    <p style="margin:0 0 24px; color:#555;">Dear {booking.payer_name},</p>

    <p style="color:#333;">Your booking <strong>{booking.booking_reference}</strong> has been cancelled.</p>

    <table width="100%" cellpadding="0" cellspacing="0" style="margin:16px 0; background-color:#fff5f5; border-radius:6px; border:1px solid #ffcdd2;">
        <tr>
            <td style="padding:16px;">
                <table width="100%" cellpadding="0" cellspacing="0">
                    <tr>
                        <td style="padding:4px 0;">
                            <strong>Route:</strong> {route.origin.name} → {route.destination.name}
                        </td>
                    </tr>
                    <tr>
                        <td style="padding:4px 0;">
                            <strong>Departure:</strong> {trip.departure_time.strftime('%A, %d %B %Y at %H:%M')}
                        </td>
                    </tr>
                    <tr>
                        <td style="padding:4px 0;">
                            <strong>Booking Ref:</strong> {booking.booking_reference}
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>

    <p style="color:#555;">If you did not request this cancellation, or if you believe this is an error, please contact our support team immediately.</p>
    <p style="color:#555;">We hope to see you on board {BRAND_NAME} again soon.</p>
    """
    return _base_template(body)
