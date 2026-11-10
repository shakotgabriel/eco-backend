import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Payment

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Payment)
def handle_payment_status_change(sender, instance, created, **kwargs):
    """
    - Payment COMPLETED → confirm the booking.
    - Payment REFUNDED  → cancel the booking (which triggers seat restore via booking signal).
    """
    booking = instance.booking

    if instance.status == "COMPLETED":
        _confirm_booking(booking)
    elif instance.status == "REFUNDED":
        _cancel_booking_if_no_completed_payments(booking)


def _confirm_booking(booking):
    if booking.status != "CONFIRMED":
        booking.status = "CONFIRMED"
        booking.save(update_fields=["status", "updated_at"])

    # Ensure all tickets are marked VALID
    booking.tickets.exclude(status="VALID").update(status="VALID")

    # Send notifications asynchronously (gracefully handle broker down)
    try:
        from bookings.tasks import (
            send_booking_confirmation_email,
            send_booking_confirmation_sms,
            send_ticket_sms,
        )
        send_booking_confirmation_email.delay(booking.pk)
        send_booking_confirmation_sms.delay(booking.pk)

        # Send individual ticket SMS to each passenger
        for ticket in booking.tickets.all():
            send_ticket_sms.delay(ticket.pk)
    except Exception as exc:
        logger.warning(f"Could not queue notifications for booking {booking.booking_reference}: {exc}")


def _cancel_booking_if_no_completed_payments(booking):
    has_completed = booking.payments.filter(status="COMPLETED").exists()
    if not has_completed and booking.status not in ("CANCELLED", "COMPLETED"):
        booking.status = "CANCELLED"
        booking.save(update_fields=["status", "updated_at"])
