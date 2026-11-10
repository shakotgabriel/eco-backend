"""HMAC-SHA256 signing and verification for ticket QR codes."""
import hashlib
import hmac
import json

from django.conf import settings


def _get_signing_key():
    """Return the signing key as bytes."""
    key = getattr(settings, "TICKET_SIGNING_KEY", settings.SECRET_KEY)
    return key.encode("utf-8") if isinstance(key, str) else key


def sign_ticket_data(data: dict) -> str:
    """
    Produce an HMAC-SHA256 signature for the ticket data dict.
    Only the security-critical fields are signed (ticket number, booking ref,
    passenger, route, departure, seat) — not the full JSON blob — so field
    ordering doesn't affect the signature.
    """
    canonical = "|".join([
        str(data.get("ticket", "")),
        str(data.get("booking", "")),
        str(data.get("passenger", "")),
        str(data.get("route", "")),
        str(data.get("departure", "")),
        str(data.get("seat", "")),
    ])
    return hmac.new(
        _get_signing_key(),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_ticket_signature(data: dict, signature: str) -> bool:
    """Verify the HMAC signature against the ticket data."""
    expected = sign_ticket_data(data)
    return hmac.compare_digest(expected, signature)


def ticket_download_token(ticket_number: str) -> str:
    """Per-ticket token for the public PDF/QR endpoints, shown only to people who can see the booking."""
    return hmac.new(
        _get_signing_key(),
        f"ticket-download|{ticket_number}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:32]


def is_valid_download_token(ticket_number: str, token: str) -> bool:
    return bool(token) and hmac.compare_digest(ticket_download_token(ticket_number), str(token))


def build_signed_qr_payload(ticket) -> str:
    """
    Build the JSON string that goes into a ticket QR code,
    including an HMAC signature for tamper-proof verification.
    """
    booking = ticket.booking
    trip = booking.trip
    route = trip.route
    seat_num = ticket.seat.seat_number if ticket.seat else "Open"

    data = {
        "ticket": ticket.ticket_number,
        "booking": booking.booking_reference,
        "passenger": f"{ticket.first_name} {ticket.last_name}",
        "route": f"{route.origin.name} -> {route.destination.name}",
        "departure": trip.departure_time.isoformat(),
        "seat": seat_num,
    }
    data["sig"] = sign_ticket_data(data)
    return json.dumps(data)
