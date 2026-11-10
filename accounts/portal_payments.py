"""PortalSettings-backed payment method toggles."""

from __future__ import annotations

from typing import Literal

PaymentToggleKey = Literal["mpesa", "momo", "cash"]

_DEFAULT_ENABLED = True


def _payments_blob() -> dict:
    from .models import PortalSettings

    data = PortalSettings.load().data or {}
    payments = data.get("payments")
    return payments if isinstance(payments, dict) else {}


def is_payment_method_enabled(method: PaymentToggleKey) -> bool:
    """Return whether a checkout method is enabled (default True if unset)."""
    blob = _payments_blob().get(method)
    if not isinstance(blob, dict):
        return _DEFAULT_ENABLED
    enabled = blob.get("enabled")
    if enabled is None:
        return _DEFAULT_ENABLED
    return bool(enabled)


def public_payment_methods() -> dict[str, bool]:
    return {
        "mpesa": is_payment_method_enabled("mpesa"),
        "momo": is_payment_method_enabled("momo"),
        "cash": is_payment_method_enabled("cash"),
    }


def payment_disabled_response(method: PaymentToggleKey):
    from rest_framework import status
    from rest_framework.response import Response

    labels = {"mpesa": "M-Pesa", "momo": "MTN MoMo", "cash": "Cash"}
    label = labels.get(method, method)
    return Response(
        {"detail": f"{label} payments are currently disabled."},
        status=status.HTTP_403_FORBIDDEN,
    )
