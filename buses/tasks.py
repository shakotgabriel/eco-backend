import logging

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task
def check_overdue_maintenance():
    """
    Daily task: flag buses with overdue maintenance by setting their status
    to MAINTENANCE if they have any overdue service records.
    Also logs warnings for upcoming maintenance (next 3 days).
    """
    from .models import Bus, MaintenanceRecord

    now = timezone.now().date()
    overdue_buses = (
        Bus.objects.filter(
            status="ACTIVE",
            maintenance_records__next_service_date__lt=now,
            maintenance_records__next_service_date__isnull=False,
        )
        .distinct()
    )

    flagged = 0
    for bus in overdue_buses:
        overdue_records = bus.maintenance_records.filter(
            next_service_date__lt=now,
            next_service_date__isnull=False,
        )
        types = ", ".join(r.get_maintenance_type_display() for r in overdue_records[:5])
        logger.warning(
            "Bus %s (%s) has overdue maintenance: %s",
            bus.plate_number, bus.name, types,
        )
        # Auto-flag as MAINTENANCE
        bus.status = "MAINTENANCE"
        bus.save(update_fields=["status", "updated_at"])
        flagged += 1

    # Warn about upcoming in next 3 days
    upcoming_cutoff = now + timezone.timedelta(days=3)
    upcoming = MaintenanceRecord.objects.filter(
        scheduled_date__gte=now,
        scheduled_date__lte=upcoming_cutoff,
        status="SCHEDULED",
    ).select_related("bus")

    for record in upcoming:
        logger.info(
            "Upcoming maintenance: %s for %s on %s",
            record.get_maintenance_type_display(),
            record.bus.plate_number,
            record.scheduled_date,
        )

    return {"flagged_buses": flagged, "upcoming_in_3_days": upcoming.count()}
