from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count

from tickets.models import Ticket
from trips.models import Trip


class Command(BaseCommand):
    help = "Rebuild Trip.available_seats from non-cancelled ticket rows."

    def add_arguments(self, parser):
        parser.add_argument("--trip-id", type=int)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        trips = Trip.objects.select_related("bus").all()
        if options["trip_id"]:
            trips = trips.filter(pk=options["trip_id"])
        changed = 0
        for trip in trips.iterator():
            occupied = Ticket.objects.filter(
                booking__trip=trip,
                booking__status__in=["PENDING", "CONFIRMED", "COMPLETED", "RESCHEDULED"],
            ).exclude(status="CANCELLED").count()
            expected = max(0, trip.bus.capacity - occupied)
            if trip.available_seats != expected:
                changed += 1
                self.stdout.write(
                    f"Trip {trip.pk}: {trip.available_seats} -> {expected} ({occupied} active tickets)"
                )
                if not options["dry_run"]:
                    with transaction.atomic():
                        Trip.objects.select_for_update().filter(pk=trip.pk).update(available_seats=expected)
        self.stdout.write(self.style.SUCCESS(f"Reconciled {changed} trip(s)."))
