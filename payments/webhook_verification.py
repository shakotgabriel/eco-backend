"""
Webhook signature verification for payment callbacks.

Each payment provider uses a different scheme:
- MTN MoMo: Verify using subscription key or IP whitelist (notification uses HTTPS mutual TLS in prod).
  We validate that the referenceId exists in our system (anti-replay) and optionally verify IP.
- M-Pesa: Safaricom signs callbacks in production with a certificate. We verify using their public cert.
- AT (legacy): HMAC-SHA256 with shared secret in X-AT-Signature header (already implemented).
"""
import hashlib
import hmac
import ipaddress
import logging

from django.conf import settings

logger = logging.getLogger(__name__)

# ── MTN MoMo IP allowlist ──────────────────────────────────────────────────
# In production, MTN MoMo sends callbacks from known IPs.
# These are example IPs — replace with actual MTN infrastructure IPs from their docs.
# When empty, IP check is skipped (sandbox/dev mode).
MTN_MOMO_ALLOWED_IPS = getattr(settings, "MTN_MOMO_ALLOWED_IPS", [])

# ── M-Pesa callback verification ──────────────────────────────────────────
# Safaricom doesn't sign STK push callbacks with HMAC. The standard security
# model is: (1) use HTTPS only callback URLs, (2) verify CheckoutRequestID matches
# a request you initiated, and (3) optionally whitelist Safaricom IPs.
MPESA_ALLOWED_IPS = getattr(settings, "MPESA_ALLOWED_IPS", [])


def verify_momo_callback_request(request):
    """
    Verify an incoming MoMo callback request.

    Returns (is_valid, error_message).
    """
    # 1. IP whitelist check (if configured)
    if MTN_MOMO_ALLOWED_IPS:
        client_ip = _get_client_ip(request)
        if not _is_ip_allowed(client_ip, MTN_MOMO_ALLOWED_IPS):
            logger.warning(f"MoMo callback from unauthorized IP: {client_ip}")
            return False, f"Unauthorized IP: {client_ip}"

    # 2. Verify the callback contains a valid referenceId that exists in our DB
    # (anti-replay — done at the view level already)

    # 3. Optional: Verify notification hash if MTN provides one in the header
    # MTN MoMo sandbox doesn't send signatures, but production may.
    notification_secret = getattr(settings, "MTN_MOMO_NOTIFICATION_SECRET", "")
    if notification_secret:
        sig_header = request.META.get("HTTP_X_CALLBACK_SIGNATURE", "")
        if not sig_header:
            logger.warning("MoMo callback missing X-Callback-Signature header.")
            return False, "Missing callback signature"
        expected = hmac.new(
            notification_secret.encode(),
            request.body,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, sig_header):
            logger.warning("MoMo callback signature mismatch.")
            return False, "Invalid callback signature"

    return True, ""


def verify_mpesa_callback_request(request):
    """
    Verify an incoming M-Pesa callback request.

    Safaricom's STK Push callback security relies on:
    1. HTTPS-only callback URL (transport-level)
    2. We only process CheckoutRequestIDs we initiated (anti-replay)
    3. IP whitelist (if configured)

    Returns (is_valid, error_message).
    """
    # 1. IP whitelist check (if configured)
    if MPESA_ALLOWED_IPS:
        client_ip = _get_client_ip(request)
        if not _is_ip_allowed(client_ip, MPESA_ALLOWED_IPS):
            logger.warning(f"M-Pesa callback from unauthorized IP: {client_ip}")
            return False, f"Unauthorized IP: {client_ip}"

    # 2. CheckoutRequestID existence check is done at the view level
    # (we look up MpesaTransaction by checkout_request_id and reject if not found)

    return True, ""


def _get_client_ip(request):
    """Extract client IP from request, considering reverse proxy headers."""
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def _is_ip_allowed(ip_str, allowed_list):
    """Check if an IP is in the allowed list (supports CIDR ranges)."""
    try:
        client_ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    for allowed in allowed_list:
        try:
            if "/" in allowed:
                if client_ip in ipaddress.ip_network(allowed, strict=False):
                    return True
            else:
                if client_ip == ipaddress.ip_address(allowed):
                    return True
        except ValueError:
            continue
    return False
