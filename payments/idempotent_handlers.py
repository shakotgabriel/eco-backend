"""
payments/idempotent_handlers.py

Idempotent payment success handling with guard against duplicate confirmations.
"""
import logging
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


def _on_payment_success_idempotent(payment):
    """
    Atomically mark a payment as COMPLETED and update booking status.
    Safe to call multiple times (idempotent).
    
    Returns: (booking, was_newly_completed)
    """
    from payments.models import Payment
    from bookings.models import Booking
    
    # Read-check: already processed?
    if payment.status == "COMPLETED":
        logger.info(f"Payment {payment.id} already COMPLETED, skipping.")
        return (payment.booking, False)
    
    with transaction.atomic():
        # Pessimistic lock: SELECT FOR UPDATE to prevent race
        payment = Payment.objects.select_for_update().get(pk=payment.pk)
        
        # Double-check after lock acquired
        if payment.status != "PENDING":
            logger.warning(
                f"Payment {payment.id} status changed to {payment.status} "
                f"before lock acquired. Skipping."
            )
            return (payment.booking, False)
        
        # Mark as COMPLETED
        payment.status = "COMPLETED"
        payment.paid_at = timezone.now()
        payment.save(update_fields=["status", "paid_at", "updated_at"])
        
        # Update booking status (this will trigger the signal)
        booking = payment.booking
        booking.status = "CONFIRMED"
        booking.expires_at = None  # No longer needs expiry
        booking.save(update_fields=["status", "expires_at", "updated_at"])
        
        logger.info(
            f"✓ Payment {payment.id} → COMPLETED, "
            f"booking {booking.booking_reference} → CONFIRMED"
        )
        
        # Trigger async notifications
        try:
            from bookings.tasks import send_booking_confirmation_email, send_booking_confirmation_sms
            send_booking_confirmation_email.delay(booking.pk)
            send_booking_confirmation_sms.delay(booking.pk)
        except Exception as e:
            logger.error(f"Failed to queue notifications for booking {booking.pk}: {e}")
        
        return (booking, True)


def _on_payment_failed_idempotent(payment, reason=""):
    """
    Mark a payment as FAILED. Idempotent.
    """
    from payments.models import Payment
    
    if payment.status == "FAILED":
        logger.info(f"Payment {payment.id} already FAILED, skipping.")
        return
    
    with transaction.atomic():
        payment = Payment.objects.select_for_update().get(pk=payment.pk)
        
        if payment.status != "PENDING":
            logger.info(
                f"Payment {payment.id} already {payment.status}, "
                f"not changing to FAILED."
            )
            return
        
        payment.status = "FAILED"
        if reason:
            payment.transaction_ref = f"{payment.transaction_ref or ''} [REASON: {reason}]"
        payment.save(update_fields=["status", "transaction_ref", "updated_at"])
        
        # DO NOT cancel the booking here — let user retry or wait for expiry
        # Cancellation should be explicit or time-based, not automatic on payment failure
        
        logger.warning(
            f"Payment {payment.id} → FAILED ({reason or 'no reason given'})"
        )
