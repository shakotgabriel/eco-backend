from django.test import TestCase

from buses.models import Bus
from buses.seat_sync import apply_explicit_seat_plan, build_seat_plan, sync_bus_seats
from seats.models import Seat


class SeatSyncTests(TestCase):
    def test_plan_vip_then_regular(self):
        plan = build_seat_plan("2+2", 2, 2)
        self.assertEqual(len(plan), 4)
        self.assertEqual([item["seat_type"] for item in plan], ["VIP", "VIP", "REGULAR", "REGULAR"])
        self.assertEqual(plan[0]["seat_number"], "1")

    def test_sync_creates_seats_from_counts(self):
        bus = Bus.objects.create(
            plate_number="TEST-001",
            name="Test Bus",
            capacity=6,
            bus_type="COMFORT_CRUISER",
            seat_layout="2+1",
            vip_seats=2,
            regular_seats=4,
        )
        result = sync_bus_seats(bus)
        self.assertEqual(result["created"], 6)
        self.assertEqual(bus.seats.filter(seat_type="VIP", is_active=True).count(), 2)
        self.assertEqual(bus.seats.filter(seat_type="REGULAR", is_active=True).count(), 4)

    def test_reducing_unused_seats_deactivates_extras(self):
        bus = Bus.objects.create(
            plate_number="TEST-002",
            name="Shrink Bus",
            capacity=4,
            seat_layout="2+2",
            vip_seats=0,
            regular_seats=4,
        )
        sync_bus_seats(bus)
        bus.regular_seats = 2
        bus.capacity = 2
        bus.save()
        result = sync_bus_seats(bus)
        self.assertEqual(result["deactivated"], 2)
        self.assertEqual(bus.seats.filter(is_active=True).count(), 2)
        self.assertEqual(Seat.objects.filter(bus=bus).count(), 4)

    def test_explicit_plan_can_put_vip_at_the_back(self):
        bus = Bus.objects.create(
            plate_number="TEST-003",
            name="Custom Map",
            capacity=4,
            seat_layout="2+2",
            vip_seats=0,
            regular_seats=4,
        )
        sync_bus_seats(bus)
        plan = [
            {"seat_number": "1", "row": 1, "column": "A", "seat_type": "REGULAR", "is_active": True},
            {"seat_number": "2", "row": 1, "column": "B", "seat_type": "REGULAR", "is_active": True},
            {"seat_number": "3", "row": 1, "column": "C", "seat_type": "REGULAR", "is_active": True},
            {"seat_number": "4", "row": 1, "column": "D", "seat_type": "VIP", "is_active": True},
        ]
        apply_explicit_seat_plan(bus, "2+2", plan)
        bus.refresh_from_db()
        self.assertEqual(bus.vip_seats, 1)
        self.assertEqual(bus.regular_seats, 3)
        rear = bus.seats.get(row=1, column="D")
        self.assertEqual(rear.seat_type, "VIP")

    def test_explicit_plan_can_mirror_2_plus_1_to_1_plus_2(self):
        bus = Bus.objects.create(
            plate_number="TEST-004",
            name="Mirror Bus",
            capacity=3,
            seat_layout="2+1",
            vip_seats=0,
            regular_seats=3,
        )
        sync_bus_seats(bus)
        left_window = bus.seats.get(row=1, column="A")
        right_seat = bus.seats.get(row=1, column="C")
        right_seat.seat_type = "VIP"
        right_seat.save(update_fields=["seat_type", "updated_at"])
        apply_explicit_seat_plan(
            bus,
            "1+2",
            [
                {"id": left_window.id, "seat_number": left_window.seat_number, "row": 1, "column": "A", "seat_type": "REGULAR", "is_active": True},
                {"id": right_seat.id, "seat_number": right_seat.seat_number, "row": 1, "column": "B", "seat_type": "VIP", "is_active": True},
                {"seat_number": "1C", "row": 1, "column": "C", "seat_type": "REGULAR", "is_active": True},
            ],
        )
        bus.refresh_from_db()
        self.assertEqual(bus.seat_layout, "1+2")
        moved = bus.seats.get(id=right_seat.id)
        self.assertEqual(moved.column, "B")
        self.assertEqual(moved.seat_type, "VIP")
        self.assertEqual(bus.seats.filter(is_active=True).count(), 3)

    def test_mirrored_layout_uses_one_left_two_right(self):
        from buses.seat_sync import column_letters, parse_layout

        self.assertEqual(parse_layout("1+2"), (1, 2))
        self.assertEqual(column_letters("1+2"), ["A", "B", "C"])
        plan = build_seat_plan("1+2", 0, 3)
        self.assertEqual([item["column"] for item in plan], ["A", "B", "C"])
        self.assertEqual(plan[0]["position"], "WINDOW")
        self.assertEqual(plan[1]["position"], "AISLE")
        self.assertEqual(plan[2]["position"], "WINDOW")
