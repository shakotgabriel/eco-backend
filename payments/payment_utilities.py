"""
Utilities for payment improvements:
- Idempotent callback decorator
- Race condition prevention
- Payment retry handler
- Provider health checker
- Exponential backoff calculator
"""
import hashlib
import logging
from datetime import timedelta
from functools import wraps

from django.db import transaction
from django.utils import timezone
from django.conf import settings
from rest_framework.response import Response

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# #1: IDEMPOTENT CALLBACK DECORATOR
# ══════════════════════════════════════════════════════════════════════════


def idempotent_callback(webhook_type):
    """
    Decorator for webhook callback views. Prevents duplicate processing of the same callback.

    Usage:
        @idempotent_callback("MOMO_CALLBACK")
        def momo_callback(request):
            ...

    Requires the request body/data to have a unique 'callback_id' field.
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            from .payment_improvements import CallbackIdempotencyKey

            # Extract callback_id from data (must be in the payload)
            data = request.data
            callback_id = data.get("referenceId") or data.get("CheckoutRequestID") or data.get("transactionId")
            
            if not callback_id:
                logger.warning(f"{webhook_type} callback missing unique ID")
                return view_func(request, *args, **kwargs)

            # Check if we've already processed this callback
            existing = CallbackIdempotencyKey.objects.filter(
                callback_id=str(callback_id),
                webhook_type=webhook_type
            ).first()

            if existing:
                logger.info(f"Duplicate {webhook_type} callback: {callback_id}, returning cached response")
                return Response(existing.response_body, status=existing.response_status)

            # Execute the view
            response = view_func(request, *args, **kwargs)

            # Store the callback response for idempotency
            if response.status_code < 500:
                try:
                    transaction_ref = data.get("externalId") or data.get("MerchantRequestID") or str(callback_id)
                    CallbackIdempotencyKey.objects.create(
                        callback_id=str(callback_id),
                        webhook_type=webhook_type,
                        transaction_ref=transaction_ref,
                        response_status=response.status_code,
                        response_body=response.data if hasattr(response, "data") else {},
                    )
                except Exception as e:
                    logger.warning(f"Failed to store idempotency key: {e}")

            return response

        return wrapper
    return decorator


# ══════════════════════════════════════════════════════════════════════════
# #2: RACE CONDITION PREVENTION
# ══════════════════════════════════════════════════════════════════════════


def prevent_concurrent_payment(booking_id, payment_method):
    """
    Prevent concurrent payment creation attempts for the same booking.
    Uses database-level locking.

    Returns (allowed, error_message)
    """
    from django.db import transaction

    from bookings.models import Booking
    from .models import Payment

    with transaction.atomic():
        try:
            # Lock the booking row to prevent concurrent updates
            booking = Booking.objects.select_for_update().get(pk=booking_id)
        except Booking.DoesNotExist:
            return False, "Booking not found"

        # Check if there's already a PENDING payment with same method
        existing_pending = Payment.objects.filter(
            booking=booking,
            method=payment_method,
            status="PENDING"
        ).exists()

    if existing_pending:
        return False, f"A pending {payment_method} payment already exists for this booking"

    return True, ""


# ══════════════════════════════════════════════════════════════════════════
# #6: EXPONENTIAL BACKOFF CALCULATOR
# ══════════════════════════════════════════════════════════════════════════


def calculate_exponential_backoff(attempt_number, base_delay_seconds=5, max_delay_seconds=300, backoff_factor=2):
    """
    Calculate exponential backoff delay with jitter.

    Args:
        attempt_number: Current retry attempt (0-indexed)
        base_delay_seconds: Initial delay
        max_delay_seconds: Maximum delay to return
        backoff_factor: Exponential factor (typically 2)

    Returns:
        Delay in seconds
    """
    import random
    
    delay = min(base_delay_seconds * (backoff_factor ** attempt_number), max_delay_seconds)
    # Add jitter: ±10% of delay
    jitter = delay * 0.1 * random.random()
    return max(1, delay - jitter)


# ══════════════════════════════════════════════════════════════════════════
# #8: PAYMENT PROVIDER HEALTH CHECKER
# ══════════════════════════════════════════════════════════════════════════


def check_provider_health(provider_name):
    """
    Check if a payment provider is healthy by attempting a lightweight API call.
    Updates PaymentProviderHealth status.

    Returns (is_healthy, error_message, response_time_ms)
    """
    from .payment_improvements import PaymentProviderHealth
    import time

    start_time = time.time()
    is_healthy = False
    error_msg = ""
    response_time_ms = 0

    try:
        if provider_name == "MTN_MOMO":
            from .mtn_momo import get_client
            client = get_client()
            # Lightweight health check: validate account holder (always use a test number)
            test_number = getattr(settings, "MOMO_TEST_PHONE", "256771234567")
            try:
                is_healthy = client.validate_account_holder(test_number)
            except Exception as e:
                error_msg = str(e)
                is_healthy = False

        elif provider_name == "MPESA":
            from .mpesa import get_client
            client = get_client()
            # M-Pesa health check: authenticate (AccessToken is a lightweight call)
            try:
                token = client.get_access_token()
                is_healthy = bool(token)
            except Exception as e:
                error_msg = str(e)
                is_healthy = False

        response_time_ms = int((time.time() - start_time) * 1000)

    except Exception as e:
        logger.error(f"Provider health check failed for {provider_name}: {e}")
        error_msg = str(e)
        is_healthy = False

    # Update health status in database
    try:
        provider_map = {
            "MTN_MOMO": "MTN_MOMO",
            "MPESA": "MPESA",
            "AT": "AT"
        }
        provider_db = provider_map.get(provider_name)
        if provider_db:
            health, _ = PaymentProviderHealth.objects.get_or_create(provider=provider_db)
            health.last_check_at = timezone.now()
            
            if is_healthy:
                health.status = "HEALTHY"
                health.last_success_at = timezone.now()
                health.consecutive_failures = 0
            else:
                health.consecutive_failures += 1
                if health.consecutive_failures >= 3:
                    health.status = "DEGRADED"
                if health.consecutive_failures >= 5:
                    health.status = "DOWN"
                health.last_error_at = timezone.now()
                health.error_message = error_msg
            
            health.response_time_ms = response_time_ms
            health.save()
    except Exception as e:
        logger.error(f"Failed to update health status: {e}")

    return is_healthy, error_msg, response_time_ms


# ══════════════════════════════════════════════════════════════════════════
# #9: WEBHOOK RETRY HELPER
# ══════════════════════════════════════════════════════════════════════════


def schedule_webhook_retry(webhook_type, transaction_ref, payload, error_msg, attempt_count=1):
    """
    Store a failed webhook for retry. Calculates next retry time using exponential backoff.
    """
    from .payment_improvements import WebhookRetry

    next_retry_delay = calculate_exponential_backoff(attempt_count - 1, base_delay_seconds=30)
    
    WebhookRetry.objects.create(
        webhook_type=webhook_type,
        transaction_ref=transaction_ref,
        payload=payload,
        status="PENDING",
        attempt_count=attempt_count,
        last_error=error_msg,
        next_retry_at=timezone.now() + timedelta(seconds=next_retry_delay)
    )
    
    logger.info(f"Scheduled {webhook_type} retry in {next_retry_delay}s for {transaction_ref}")


# ══════════════════════════════════════════════════════════════════════════
# #4: CUSTOMER PAYMENT RETRY HANDLER
# ══════════════════════════════════════════════════════════════════════════


def create_payment_retry(original_payment_id, new_method=None):
    """
    Allow customer to retry a failed payment. Creates a new Payment record.

    Args:
        original_payment_id: ID of the failed Payment
        new_method: Override payment method (optional)

    Returns:
        (new_payment, error_message) or (None, error_message)
    """
    from django.db import transaction

    from bookings.models import Booking
    from .models import Payment
    from .payment_improvements import PaymentRetryLog

    with transaction.atomic():
        try:
            original_payment = Payment.objects.select_related("booking").get(pk=original_payment_id)
        except Payment.DoesNotExist:
            return None, "Payment not found"

        booking = Booking.objects.select_for_update().get(pk=original_payment.booking_id)

        # Can only retry FAILED payments
        if original_payment.status not in ("FAILED",):
            return None, f"Cannot retry a {original_payment.status} payment. Only FAILED payments can be retried."

        # Booking must still be PENDING
        if booking.status != "PENDING":
            return None, f"Booking is {booking.status}. Can only retry if booking is still PENDING."

        if booking.payments.filter(status="COMPLETED").exists():
            return None, "This booking is already paid."
        if booking.payments.filter(status="PENDING").exists():
            return None, "A payment attempt is already in progress for this booking."

        # Create new payment with same amount but potentially different method
        new_payment = Payment.objects.create(
            booking=booking,
            amount=original_payment.amount,
            currency=original_payment.currency,
            method=new_method or original_payment.method,
            status="PENDING"
        )

        # Log the retry attempt
        retry_count = original_payment.retry_logs.count() + 1
        PaymentRetryLog.objects.create(
            payment=original_payment,
            original_attempt_at=original_payment.created_at,
            retry_attempt_number=retry_count,
            new_payment_id=new_payment.id,
            status="PENDING"
        )

    logger.info(f"Created payment retry: original={original_payment_id}, new={new_payment.id}, method={new_payment.method}")

    return new_payment, ""
