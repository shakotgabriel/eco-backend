"""
Management command to seed the actual EcoBus fleet.

Fleet:
  3x Executive Cruiser (VIP) — 12 VIP + 25 Regular = 37 seats
  3x Cargo Cruiser           — 12 VIP + cargo area  = 12 passenger seats
  20x Comfort Cruiser        — 53 Regular seats

Usage:
  python manage.py seed_fleet
"""
from django.core.management.base import BaseCommand

from buses.models import Bus
from seats.models import Seat


FLEET = [
    # (name_prefix, bus_type, capacity, vip_seats, regular_seats, has_cargo, layout, total_rows, count)
    ("Executive Cruiser", "EXECUTIVE_CRUISER", 37, 12, 25, False, "2+2", 10, 3),
    ("Cargo Cruiser", "CARGO_CRUISER", 12, 12, 0, True, "2+1", 4, 3),
    ("Comfort Cruiser", "COMFORT_CRUISER", 53, 0, 53, False, "2+3", 11, 20),
]


def _column_letters(layout):
    """Return column letters based on layout like '2+2' → ['A','B','C','D']."""
    left, right = layout.split("+")
    cols = []
    for i in range(int(left)):
        cols.append(chr(65 + i))  # A, B
    for i in range(int(right)):
        cols.append(chr(65 + int(left) + i))  # C, D
    return cols


def _position(col_idx, total_cols):
    """Determine seat position: WINDOW for edge, AISLE for inner, MIDDLE otherwise."""
    if col_idx == 0 or col_idx == total_cols - 1:
        return "WINDOW"
    return "AISLE"


class Command(BaseCommand):
    help = "Seed the EcoBus fleet with real bus configurations"

    def add_arguments(self, parser):
        parser.add_argument("--clear", action="store_true", help="Delete existing buses and seats first")

    def handle(self, *args, **options):
        if options["clear"]:
            self.stdout.write("Clearing existing fleet...")
            Seat.objects.all().delete()
            Bus.objects.all().delete()

        created_buses = 0
        created_seats = 0

        for (name_prefix, bus_type, capacity, vip_count, regular_count,
             has_cargo, layout, total_rows, count) in FLEET:

            for i in range(1, count + 1):
                plate = f"BEB-{bus_type[:4]}-{i:03d}"
                name = f"{name_prefix} {i:02d}"

                if Bus.objects.filter(plate_number=plate).exists():
                    self.stdout.write(f"  Skipping {plate} (exists)")
                    continue

                bus = Bus.objects.create(
                    plate_number=plate,
                    name=name,
                    capacity=capacity,
                    bus_type=bus_type,
                    seat_layout=layout,
                    total_rows=total_rows,
                    has_cargo_section=has_cargo,
                    vip_seats=vip_count,
                    regular_seats=regular_count,
                )
                created_buses += 1

                # Generate seats
                columns = _column_letters(layout)
                seat_num = 1

                # VIP seats first (front rows)
                vip_remaining = vip_count
                row = 1
                while vip_remaining > 0:
                    for col_idx, col in enumerate(columns):
                        if vip_remaining <= 0:
                            break
                        Seat.objects.create(
                            bus=bus,
                            seat_number=str(seat_num),
                            row=row,
                            column=col,
                            position=_position(col_idx, len(columns)),
                            seat_type="VIP",
                        )
                        seat_num += 1
                        vip_remaining -= 1
                        created_seats += 1
                    row += 1

                # Regular seats (remaining rows)
                regular_remaining = regular_count
                while regular_remaining > 0:
                    for col_idx, col in enumerate(columns):
                        if regular_remaining <= 0:
                            break
                        Seat.objects.create(
                            bus=bus,
                            seat_number=str(seat_num),
                            row=row,
                            column=col,
                            position=_position(col_idx, len(columns)),
                            seat_type="REGULAR",
                        )
                        seat_num += 1
                        regular_remaining -= 1
                        created_seats += 1
                    row += 1

                self.stdout.write(f"  ✓ {name} ({plate}) — {vip_count} VIP + {regular_count} Regular")

        self.stdout.write(self.style.SUCCESS(
            f"\nDone! Created {created_buses} buses and {created_seats} seats."
        ))
