import logging

from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_booking_confirmation_email(self, booking_id):
    """Send branded HTML booking confirmation with PDF ticket attachments via SendGrid."""
    from .models import Booking

    try:
        booking = Booking.objects.select_related(
            "trip__route__origin", "trip__route__destination", "trip__bus"
        ).get(pk=booking_id)
    except Booking.DoesNotExist:
        logger.error(f"Booking {booking_id} not found, skipping email.")
        return

    if not booking.email:
        logger.info(f"Booking {booking.booking_reference} has no email, skipping.")
        return

    tickets = booking.tickets.select_related("seat").all()
    trip = booking.trip

    # Build HTML content
    from bebeto_backend.services.sendgrid_email import (
        booking_confirmation_html,
        send_email,
    )

    html_content = booking_confirmation_html(booking, tickets, trip)
    subject = f"Booking Confirmed — {booking.booking_reference}"

    # Plain-text fallback
    plain = (
        f"Dear {booking.payer_name},\n\n"
        f"Your booking {booking.booking_reference} has been confirmed!\n"
        f"Route: {trip.route.origin.name} → {trip.route.destination.name}\n"
        f"Departure: {timezone.localtime(trip.departure_time).strftime('%a, %d %b %Y at %H:%M')}\n"
        f"Total: {trip.route.currency} {booking.total_price}\n\n"
        f"Please arrive 30 minutes early. Thank you for choosing EcoBus!"
    )

    # Generate PDF tickets as attachments
    from tickets.pdf import generate_ticket_pdf

    attachments = []
    for ticket in tickets:
        try:
            pdf_buf = generate_ticket_pdf(ticket)
            attachments.append({
                "content": pdf_buf.read(),
                "filename": f"{ticket.ticket_number}.pdf",
                "type": "application/pdf",
            })
        except Exception as exc:
            logger.warning(f"Could not generate PDF for ticket {ticket.ticket_number}: {exc}")

    try:
        send_email(
            to_email=booking.email,
            subject=subject,
            html_content=html_content,
            plain_content=plain,
            attachments=attachments if attachments else None,
        )
        logger.info(f"Confirmation email sent for booking {booking.booking_reference}")
    except Exception as exc:
        logger.error(f"Failed to send email for booking {booking.booking_reference}: {exc}")
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_booking_cancellation_email(self, booking_id):
    """Send branded HTML cancellation email via SendGrid."""
    from .models import Booking

    try:
        booking = Booking.objects.select_related(
            "trip__route__origin", "trip__route__destination"
        ).get(pk=booking_id)
    except Booking.DoesNotExist:
        return

    if not booking.email:
        return

    from bebeto_backend.services.sendgrid_email import (
        booking_cancellation_html,
        send_email,
    )

    trip = booking.trip
    html_content = booking_cancellation_html(booking, trip)
    subject = f"Booking Cancelled — {booking.booking_reference}"

    plain = (
        f"Dear {booking.payer_name},\n\n"
        f"Your booking {booking.booking_reference} for "
        f"{trip.route.origin.name} → {trip.route.destination.name} "
        f"on {timezone.localtime(trip.departure_time).strftime('%a, %d %b %Y at %H:%M')} "
        f"has been cancelled.\n\n"
        f"Contact support if this is an error.\nEcoBus"
    )

    try:
        send_email(
            to_email=booking.email,
            subject=subject,
            html_content=html_content,
            plain_content=plain,
        )
    except Exception as exc:
        raise self.retry(exc=exc)


# ─── SMS Tasks (Twilio) ─────────────────────────────────────────────────────


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_booking_confirmation_sms(self, booking_id):
    """Send SMS confirmation to the payer after payment is completed."""
    from bebeto_backend.services.twilio_sms import notify_customer
    from .models import Booking

    try:
        booking = Booking.objects.select_related(
            "trip__route__origin", "trip__route__destination"
        ).get(pk=booking_id)
    except Booking.DoesNotExist:
        logger.error(f"Booking {booking_id} not found, skipping SMS.")
        return

    trip = booking.trip
    message = (
        f"EcoBus: Booking {booking.booking_reference} confirmed!\n"
        f"{trip.route.origin.name} -> {trip.route.destination.name}\n"
        f"Departure: {timezone.localtime(trip.departure_time).strftime('%d %b %Y %H:%M')}\n"
        f"Seats: {booking.number_of_seats} | Total: {booking.total_price}\n"
        f"Tickets: {settings.PUBLIC_SITE_URL}/manage-booking\n"
        f"Arrive 30min early. Tickets are non-refundable."
    )

    try:
        notify_customer(booking.payment_phone, message)
    except Exception as exc:
        logger.error(f"SMS failed for booking {booking.booking_reference}: {exc}")
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_booking_cancellation_sms(self, booking_id):
    """Notify the payer via SMS that their booking has been cancelled."""
    from bebeto_backend.services.twilio_sms import notify_customer
    from .models import Booking

    try:
        booking = Booking.objects.select_related(
            "trip__route__origin", "trip__route__destination"
        ).get(pk=booking_id)
    except Booking.DoesNotExist:
        return

    trip = booking.trip
    message = (
        f"EcoBus: Booking {booking.booking_reference} "
        f"({trip.route.origin.name} -> {trip.route.destination.name}) "
        f"has been cancelled and the seats released. Tickets are non-refundable. "
        f"Contact support if this is an error."
    )

    try:
        notify_customer(booking.payment_phone, message)
    except Exception as exc:
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_ticket_sms(self, ticket_id):
    """Send individual ticket details via SMS to each passenger."""
    from bebeto_backend.services.twilio_sms import notify_customer
    from tickets.models import Ticket

    try:
        ticket = Ticket.objects.select_related(
            "booking__trip__route__origin", "booking__trip__route__destination"
        ).get(pk=ticket_id)
    except Ticket.DoesNotExist:
        return

    trip = ticket.booking.trip
    message = (
        f"EcoBus Ticket: {ticket.ticket_number}\n"
        f"{ticket.first_name} {ticket.last_name}\n"
        f"{trip.route.origin.name} -> {trip.route.destination.name}\n"
        f"Departure: {timezone.localtime(trip.departure_time).strftime('%d %b %Y %H:%M')}\n"
        f"Class: {ticket.seat_class} | Ref: {ticket.booking.booking_reference}"
    )

    try:
        notify_customer(ticket.phone_number, message)
    except Exception as exc:
        raise self.retry(exc=exc)


@shared_task
def send_pre_expiry_warnings():
    """
    Warn customers that their PENDING booking is about to expire.
    Targets bookings expiring in 2-3 minutes (run every minute via Beat).
    """
    from datetime import timedelta
    from django.utils import timezone
    from bebeto_backend.services.twilio_sms import notify_customer
    from .models import Booking

    now = timezone.now()
    window_start = now + timedelta(seconds=90)
    window_end = now + timedelta(seconds=150)

    expiring = Booking.objects.filter(
        status="PENDING",
        expires_at__gte=window_start,
        expires_at__lt=window_end,
    )

    sent = 0
    for booking in expiring:
        if booking.payment_phone:
            try:
                notify_customer(
                    str(booking.payment_phone),
                    f"EcoBus: Your booking {booking.booking_reference} expires in ~2 minutes! "
                    f"Complete payment now to secure your seats.",
                )
                sent += 1
            except Exception as exc:
                logger.warning(f"Pre-expiry SMS failed for {booking.booking_reference}: {exc}")

    if sent:
        logger.info(f"Sent {sent} pre-expiry warning(s).")
    return {"warnings_sent": sent}


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_trip_reminder_sms(self, booking_id):
    """Send trip reminder SMS 2 hours before departure."""
    from bebeto_backend.services.twilio_sms import notify_customer
    from .models import Booking

    try:
        booking = Booking.objects.select_related(
            "trip__route__origin", "trip__route__destination"
        ).get(pk=booking_id, status="CONFIRMED")
    except Booking.DoesNotExist:
        return

    trip = booking.trip
    message = (
        f"EcoBus Reminder: Your trip "
        f"{trip.route.origin.name} -> {trip.route.destination.name} "
        f"departs at {timezone.localtime(trip.departure_time).strftime('%H:%M')} today. "
        f"Ref: {booking.booking_reference}. Please arrive 30min early!"
    )

    try:
        notify_customer(booking.payment_phone, message)
    except Exception as exc:
        raise self.retry(exc=exc)
