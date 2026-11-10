from datetime import time
from decimal import Decimal

from django.core.management.base import BaseCommand

from buses.models import Bus
from cities.models import City
from routes.models import Route
from trips.models import TripSchedule


# (origin_code, dest_code, bus_plate, departure, regular_price)
SCHEDULES = [
    # Nairobi → Kampala: 5pm and 7pm
    ("NBO", "KLA", "BEB-COMF-001", time(17, 0), Decimal("3500")),
    ("NBO", "KLA", "BEB-COMF-002", time(19, 0), Decimal("3500")),
    # Nairobi → Juba: 6am (Cargo)
    ("NBO", "JUB", "BEB-CARG-001", time(6, 0), Decimal("10000")),
    # Kampala → Nairobi: 5pm and 7pm
    ("KLA", "NBO", "BEB-COMF-003", time(17, 0), Decimal("120000")),
    ("KLA", "NBO", "BEB-COMF-004", time(19, 0), Decimal("120000")),
    # Kampala → Juba: 5pm and 7pm
    ("KLA", "JUB", "BEB-COMF-005", time(17, 0), Decimal("250000")),
    ("KLA", "JUB", "BEB-COMF-006", time(19, 0), Decimal("250000")),
    # Juba → Kampala: 6am
    ("JUB", "KLA", "BEB-COMF-007", time(6, 0), Decimal("250000")),
    # Juba → Nairobi: 6am
    ("JUB", "NBO", "BEB-COMF-008", time(6, 0), Decimal("10000")),
]


class Command(BaseCommand):
    help = "Create daily trip schedules based on EcoBus timetable"

    def handle(self, *args, **options):
        days = [0, 1, 2, 3, 4, 5, 6]
        created = 0
        skipped = 0

        for origin_code, dest_code, plate, dep_time, price in SCHEDULES:
            try:
                origin = City.objects.get(code=origin_code)
                dest = City.objects.get(code=dest_code)
                route = Route.objects.get(origin=origin, destination=dest)
                bus = Bus.objects.get(plate_number=plate)
            except (City.DoesNotExist, Route.DoesNotExist, Bus.DoesNotExist) as exc:
                self.stdout.write(self.style.WARNING(
                    f"  Skip {origin_code}→{dest_code} {plate}: {exc}"
                ))
                continue

            _, was_created = TripSchedule.objects.get_or_create(
                route=route,
                bus=bus,
                departure_time=dep_time,
                defaults={
                    "days_of_week": days,
                    "regular_price": price,
                    "is_active": True,
                },
            )
            if was_created:
                created += 1
                self.stdout.write(f"✓ {route} @ {dep_time:%H:%M} | Bus: {bus.name}")
            else:
                skipped += 1

        self.stdout.write(self.style.SUCCESS(
            f"\nSchedules: {created} created, {skipped} already existed (daily)"
        ))
