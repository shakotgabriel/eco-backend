"""Validate seat ownership before confirming payment to prevent double-booking."""
import logging

logger = logging.getLogger(__name__)


def validate_seats_still_available(booking):
    """
    Check that all seats in this booking's tickets are not already booked
    by a *different* CONFIRMED booking on the same trip.

    Returns (is_valid, error_message).
    """
    from bookings.models import Booking

    trip = booking.trip
    ticket_seat_ids = set(
        booking.tickets.values_list("seat_id", flat=True)
    ) - {None}

    if not ticket_seat_ids:
        return True, ""

    # Seats booked by OTHER confirmed/completed bookings on the same trip
    conflict_seat_ids = set(
        Booking.objects.filter(
            trip=trip,
            status__in=("CONFIRMED", "COMPLETED"),
        )
        .exclude(pk=booking.pk)
        .values_list("tickets__seat_id", flat=True)
    ) - {None}

    overlap = ticket_seat_ids & conflict_seat_ids
    if overlap:
        logger.warning(
            f"Seat ownership conflict for booking {booking.booking_reference}: "
            f"seats {overlap} already confirmed by another booking on trip {trip.id}"
        )
        return False, f"Seats {overlap} are already confirmed on another booking."

    return True, ""
