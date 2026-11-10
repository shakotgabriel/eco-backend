"""Shared fixtures for API tests."""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from bookings.models import Booking
from buses.models import Bus
from cities.models import City
from routes.models import Route
from trips.models import Trip


def make_trip(*, departs_in=timedelta(days=2), currency="KES", plate="KBZ100A"):
    origin, _ = City.objects.get_or_create(code="NBO", defaults={"name": "Nairobi", "country": "Kenya"})
    destination, _ = City.objects.get_or_create(code="KLA", defaults={"name": "Kampala", "country": "Uganda"})
    route, _ = Route.objects.get_or_create(
        origin=origin,
        destination=destination,
        defaults={
            "distance_km": Decimal("660.00"),
            "estimated_duration": timedelta(hours=10),
            "base_price": Decimal("5000.00"),
            "currency": currency,
            "is_active": True,
        },
    )
    bus = Bus.objects.create(
        plate_number=plate,
        name=f"Bus {plate}",
        home_city=origin,
        capacity=48,
        bus_type="COMFORT_CRUISER",
        seat_layout="2+2",
        total_rows=12,
        regular_seats=48,
        status="ACTIVE",
    )
    departure = timezone.now() + departs_in
    return Trip.objects.create(
        route=route,
        bus=bus,
        departure_time=departure,
        arrival_time=departure + timedelta(hours=10),
        regular_price=Decimal("5000.00"),
        available_seats=46,
    )


def make_booking(trip, *, status="CONFIRMED", phone="+254712345678", seats=2):
    return Booking.objects.create(
        trip=trip,
        payer_name="Grace Akol",
        payment_phone=phone,
        number_of_seats=seats,
        total_price=Decimal("5000.00") * seats,
        status=status,
    )
