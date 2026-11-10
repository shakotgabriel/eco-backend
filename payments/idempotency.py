"""Idempotency key support for payment endpoints to prevent double charges."""
import hashlib
import json
import logging
from functools import wraps

from rest_framework.response import Response

logger = logging.getLogger(__name__)

IDEMPOTENCY_HEADER = "HTTP_IDEMPOTENCY_KEY"  # Idempotency-Key header


def require_idempotency_key(view_func):
    """
    Decorator for payment views. If the request includes an ``Idempotency-Key``
    header, we either:
      1. Return the cached response if this key was already used, OR
      2. Execute the view, store the response, and return it.

    If no header is present the request proceeds normally (backwards-compatible).
    """

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        key_value = request.META.get(IDEMPOTENCY_HEADER, "").strip()
        if not key_value:
            return view_func(request, *args, **kwargs)

        from .models import IdempotencyKey

        endpoint = request.path

        # Hash the request body so we can detect mismatched replays
        body_hash = hashlib.sha256(request.body).hexdigest()

        # Check for existing key
        existing = IdempotencyKey.objects.filter(key=key_value, endpoint=endpoint).first()
        if existing:
            if existing.request_hash != body_hash:
                return Response(
                    {"detail": "Idempotency key already used with a different request body."},
                    status=422,
                )
            logger.info(f"Idempotency hit: key={key_value}, endpoint={endpoint}")
            return Response(existing.response_body, status=existing.response_status)

        # Execute the view
        response = view_func(request, *args, **kwargs)

        # Store the response for this key (only for success/client-error, not 5xx)
        if response.status_code < 500:
            try:
                IdempotencyKey.objects.create(
                    key=key_value,
                    endpoint=endpoint,
                    request_hash=body_hash,
                    response_status=response.status_code,
                    response_body=response.data if hasattr(response, "data") else {},
                )
            except Exception:
                # If there's a race condition (duplicate key), it's fine
                pass

        return response

    return wrapper
