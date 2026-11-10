from django.db.models import F
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from .models import Booking


@receiver(pre_save, sender=Booking)
def track_previous_status(sender, instance, **kwargs):
    """Stash the old status so post_save can detect transitions."""
    if instance.pk:
        try:
            instance._prev_status = Booking.objects.values_list("status", flat=True).get(pk=instance.pk)
        except Booking.DoesNotExist:
            instance._prev_status = None
    else:
        instance._prev_status = None


@receiver(post_save, sender=Booking)
def handle_booking_status_change(sender, instance, created, **kwargs):
    """
    Seat inventory is reserved inside BookingCreateSerializer (with row locks).
    This signal only handles CANCELLED transitions: restore seats, cancel tickets,
    and fail pending payments.
    """
    if created:
        try:
            from reports.audit import log_action
            log_action("BOOKING_CREATED", instance)
        except Exception:
            pass
        return

    prev = getattr(instance, "_prev_status", None)
    if instance.status == "CANCELLED" and prev != "CANCELLED":
        _cancel_booking(instance)

        try:
            from reports.audit import log_action
            log_action("BOOKING_CANCELLED", instance, details={"prev_status": prev})
        except Exception:
            pass

        try:
            from .tasks import send_booking_cancellation_email, send_booking_cancellation_sms
            send_booking_cancellation_email.delay(instance.pk)
            send_booking_cancellation_sms.delay(instance.pk)
        except Exception:
            pass  # Broker down — cancellation still applied, notifications skipped


def _cancel_booking(booking):
    from seats.models import SeatLock
    from trips.models import Trip

    # Restore seats on the trip (only once per cancellation)
    Trip.objects.filter(pk=booking.trip_id).update(
        available_seats=F("available_seats") + booking.number_of_seats
    )

    # Cancel all tickets for this booking
    booking.tickets.exclude(status="CANCELLED").update(status="CANCELLED")

    # Clear any leftover seat holds for this trip that belong to open sessions
    # (desk/website locks are session-based; expired ones are cleaned elsewhere)
    try:
        SeatLock.objects.filter(
            trip_id=booking.trip_id,
            seat_id__in=booking.tickets.values_list("seat_id", flat=True),
        ).delete()
    except Exception:
        pass

    # Fail pending payments and their linked mobile money transactions
    pending_payments = booking.payments.filter(status="PENDING")
    for payment in pending_payments:
        payment.status = "FAILED"
        payment.save(update_fields=["status", "updated_at"])

        # Fail linked M-Pesa transactions
        try:
            from payments.models import MpesaTransaction
            MpesaTransaction.objects.filter(
                payment=payment, mpesa_status="PENDING"
            ).update(mpesa_status="CANCELLED", result_desc="Booking cancelled")
        except Exception:
            pass

        # Fail linked MoMo transactions
        try:
            from payments.models import MoMoTransaction
            MoMoTransaction.objects.filter(
                payment=payment, momo_status="PENDING"
            ).update(momo_status="FAILED", reason="Booking cancelled")
        except Exception:
            pass
