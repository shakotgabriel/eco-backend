import logging

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task
def update_trip_statuses():
    """
    Periodic task — auto-transition trip statuses based on time:
      SCHEDULED → BOARDING  (30 min before departure)
      BOARDING  → DEPARTED  (at departure time)
      DEPARTED  → ARRIVED   (at arrival time)

    Also cancel PENDING bookings on departed trips that were never paid.
    """
    from bookings.models import Booking
    from .models import Trip

    now = timezone.now()

    # SCHEDULED → BOARDING (30 min before departure)
    boarding_count = Trip.objects.filter(
        status="SCHEDULED",
        departure_time__lte=now + timezone.timedelta(minutes=30),
    ).update(status="BOARDING")

    # BOARDING → DEPARTED (past departure time)
    departed_count = Trip.objects.filter(
        status="BOARDING",
        departure_time__lte=now,
    ).update(status="DEPARTED")

    # DEPARTED → ARRIVED (past arrival time)
    arrived_count = Trip.objects.filter(
        status="DEPARTED",
        arrival_time__lte=now,
    ).update(status="ARRIVED")

    # Auto-cancel unpaid bookings on departed/arrived trips
    stale_bookings = Booking.objects.filter(
        status="PENDING",
        trip__status__in=["DEPARTED", "ARRIVED"],
    )
    cancelled_count = 0
    for booking in stale_bookings:
        booking.status = "CANCELLED"
        booking.save(update_fields=["status", "updated_at"])
        cancelled_count += 1

    if any([boarding_count, departed_count, arrived_count, cancelled_count]):
        logger.info(
            f"Trip status update: {boarding_count} boarding, {departed_count} departed, "
            f"{arrived_count} arrived, {cancelled_count} stale bookings cancelled."
        )

    return {
        "boarding": boarding_count,
        "departed": departed_count,
        "arrived": arrived_count,
        "stale_bookings_cancelled": cancelled_count,
    }


@shared_task
def complete_arrived_trips():
    """
    Mark ARRIVED trips as completed and complete their confirmed bookings.
    Runs less frequently (e.g. every hour).
    """
    from bookings.models import Booking
    from .models import Trip

    now = timezone.now()

    # Trips that arrived more than 1 hour ago
    arrived_trips = Trip.objects.filter(
        status="ARRIVED",
        arrival_time__lte=now - timezone.timedelta(hours=1),
    )

    trip_ids = list(arrived_trips.values_list("pk", flat=True))
    if not trip_ids:
        return {"completed_trips": 0, "completed_bookings": 0}

    trip_count = arrived_trips.update(status="COMPLETED")

    booking_count = Booking.objects.filter(
        trip_id__in=trip_ids,
        status="CONFIRMED",
    ).update(status="COMPLETED")

    logger.info(f"Completed {trip_count} trips and {booking_count} bookings.")

    return {"completed_trips": trip_count, "completed_bookings": booking_count}


@shared_task
def expire_pending_bookings():
    """
    Auto-cancel PENDING bookings that have passed their expires_at.
    Also cleans up expired seat locks.
    Runs every minute via Celery Beat.
    """
    from bookings.models import Booking
    from seats.models import SeatLock

    now = timezone.now()

    # Expire stale bookings
    stale = Booking.objects.filter(status="PENDING", expires_at__lte=now)
    expired_count = 0
    for booking in stale:
        booking.status = "CANCELLED"
        booking.save(update_fields=["status", "updated_at"])
        expired_count += 1

    # Clean expired seat locks
    lock_count, _ = SeatLock.objects.filter(expires_at__lte=now).delete()

    if expired_count or lock_count:
        logger.info(f"Expired {expired_count} bookings, released {lock_count} seat locks.")

    return {"expired_bookings": expired_count, "released_locks": lock_count}


@shared_task
def generate_scheduled_trips(
    days_ahead=7,
    dry_run=False,
    route_id=None,
    origin_city_id=None,
    destination_city_id=None,
    schedule_ids=None,
):
    """
    Generate Trip instances from active TripSchedule entries.

    Manager-triggered via POST /trips/schedules/generate-now/.
    Set dry_run=True to preview without creating rows.
    """
    from datetime import datetime, timedelta

    from .models import Trip, TripSchedule

    try:
        days_ahead = int(days_ahead)
    except (TypeError, ValueError):
        days_ahead = 7
    days_ahead = max(1, min(days_ahead, 60))
    dry_run = bool(dry_run)

    now = timezone.now()
    schedules = TripSchedule.objects.filter(is_active=True).select_related(
        "route__origin", "route__destination", "bus", "driver"
    )
    if route_id:
        schedules = schedules.filter(route_id=route_id)
    if origin_city_id:
        schedules = schedules.filter(route__origin_id=origin_city_id)
    if destination_city_id:
        schedules = schedules.filter(route__destination_id=destination_city_id)
    if schedule_ids:
        schedules = schedules.filter(id__in=list(schedule_ids))

    schedule_list = list(schedules)
    created_count = 0
    skipped_existing = 0
    preview = []

    for schedule in schedule_list:
        route_display = str(schedule.route)
        for offset in range(days_ahead):
            target_date = (now + timedelta(days=offset)).date()
            weekday = target_date.weekday()  # 0=Mon … 6=Sun

            if weekday not in (schedule.days_of_week or []):
                continue

            dep_dt = timezone.make_aware(
                datetime.combine(target_date, schedule.departure_time)
            )
            arr_dt = dep_dt + schedule.route.estimated_duration

            already_exists = Trip.objects.filter(
                route=schedule.route,
                bus=schedule.bus,
                departure_time=dep_dt,
            ).exists()
            if already_exists:
                skipped_existing += 1
                if dry_run:
                    preview.append({
                        "schedule_id": schedule.id,
                        "route_id": schedule.route_id,
                        "route_display": route_display,
                        "bus_id": schedule.bus_id,
                        "bus_display": str(schedule.bus),
                        "departure_time": dep_dt.isoformat(),
                        "arrival_time": arr_dt.isoformat(),
                        "action": "skip_existing",
                    })
                continue

            entry = {
                "schedule_id": schedule.id,
                "route_id": schedule.route_id,
                "route_display": route_display,
                "bus_id": schedule.bus_id,
                "bus_display": str(schedule.bus),
                "departure_time": dep_dt.isoformat(),
                "arrival_time": arr_dt.isoformat(),
                "action": "create",
            }
            if dry_run:
                preview.append(entry)
                created_count += 1
                continue

            Trip.objects.create(
                route=schedule.route,
                bus=schedule.bus,
                driver=schedule.driver,
                departure_time=dep_dt,
                arrival_time=arr_dt,
                regular_price=schedule.regular_price,
                vip_price=schedule.vip_price,
                cargo_price_per_kg=schedule.cargo_price_per_kg,
                available_seats=schedule.bus.capacity,
            )
            created_count += 1

    if created_count and not dry_run:
        logger.info(
            "Generated %s trips from schedules (days_ahead=%s).",
            created_count,
            days_ahead,
        )

    result = {
        "trips_created": 0 if dry_run else created_count,
        "trips_would_create": created_count if dry_run else created_count,
        "trips_skipped_existing": skipped_existing,
        "schedules_considered": len(schedule_list),
        "days_ahead": days_ahead,
        "dry_run": dry_run,
    }
    if dry_run:
        # Cap preview payload size for the UI
        result["preview"] = preview[:200]
        result["preview_truncated"] = len(preview) > 200
    return result


@shared_task
def send_trip_reminders():
    """
    Periodic task — send SMS reminders for trips departing in 2-3 hours.
    Should run every 30 minutes via Celery Beat.
    """
    from bookings.models import Booking
    from bookings.tasks import send_trip_reminder_sms

    now = timezone.now()
    window_start = now + timezone.timedelta(hours=2)
    window_end = now + timezone.timedelta(hours=3)

    upcoming_bookings = Booking.objects.filter(
        status="CONFIRMED",
        trip__status__in=["SCHEDULED", "BOARDING"],
        trip__departure_time__gte=window_start,
        trip__departure_time__lt=window_end,
    )

    count = 0
    for booking in upcoming_bookings:
        send_trip_reminder_sms.delay(booking.pk)
        count += 1

    if count:
        logger.info(f"Queued {count} trip reminder SMS(es).")

    return {"reminders_sent": count}


@shared_task(bind=True, max_retries=2, default_retry_delay=30)
def notify_trip_status_change(self, trip_id, new_status):
    """
    Notify all passengers of a trip when its status changes
    (e.g., CANCELLED or DEPARTED).
    """
    from bebeto_backend.services.twilio_sms import send_sms
    from bookings.models import Booking

    from .models import Trip

    try:
        trip = Trip.objects.select_related("route__origin", "route__destination").get(pk=trip_id)
    except Trip.DoesNotExist:
        return

    route_str = f"{trip.route.origin.name} → {trip.route.destination.name}"
    departure_str = trip.departure_time.strftime("%d %b %Y %H:%M")

    if new_status == "CANCELLED":
        message = (
            f"EcoBus: Your trip {route_str} on {departure_str} has been CANCELLED. "
            f"Please contact support for rebooking or refund. We apologize for the inconvenience."
        )
    elif new_status == "DEPARTED":
        message = (
            f"EcoBus: Trip {route_str} has DEPARTED. "
            f"Safe travels! If you missed your bus, contact support."
        )
    else:
        message = f"EcoBus: Trip {route_str} status updated to {new_status}."

    # Get all confirmed bookings on this trip
    bookings = Booking.objects.filter(
        trip=trip,
        status__in=("CONFIRMED", "PENDING"),
    ).values_list("payment_phone", flat=True).distinct()

    sent = 0
    for phone in bookings:
        if phone:
            try:
                send_sms(str(phone), message)
                sent += 1
            except Exception as exc:
                logger.warning(f"Failed to send trip notification to {phone}: {exc}")

    logger.info(f"Trip {trip_id} status → {new_status}: sent {sent} SMS notifications.")
    return {"trip_id": trip_id, "status": new_status, "notifications_sent": sent}
