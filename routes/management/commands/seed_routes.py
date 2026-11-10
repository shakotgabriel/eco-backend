"""
Seed cities and routes for EcoBus.

Corridor: Juba – Kigali – Kampala – Nairobi (+ Kakuma)

Cities: Juba (SS), Nairobi (KE), Kampala (UG), Kigali (RW), Kakuma (KE)

Routes & Pricing (base_price = regular/economic, see Trip for VIP):
  Kampala → Nairobi   VIP 140,000 UGX  Regular 120,000 UGX
  Nairobi → Kampala   VIP  5,000 KES   Regular  3,500 KES
  Nairobi → Juba      VIP 10,000 KES   (Cargo Cruiser, VIP only)
  Nairobi → Kakuma    VIP  4,000 KES
  Nairobi → Kigali    VIP  8,500 KES   Regular  7,000 KES
  Kigali  → Nairobi   VIP 95,000 RWF   Regular 80,000 RWF
  Kigali  → Kampala   VIP 200,000 UGX  Regular 140,000 UGX
  Juba    → Kampala   VIP 350,000 SSP  Regular 250,000 SSP

Usage:
  python manage.py seed_routes
  python manage.py seed_routes --clear
"""
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand

from cities.models import City
from routes.models import Route


CITIES = [
    {"name": "Juba", "code": "JUB", "country": "South Sudan"},
    {"name": "Nairobi", "code": "NBO", "country": "Kenya"},
    {"name": "Kampala", "code": "KLA", "country": "Uganda"},
    {"name": "Kigali", "code": "KGL", "country": "Rwanda"},
    {"name": "Kakuma", "code": "KAK", "country": "Kenya"},
]

# (origin_code, dest_code, distance_km, duration_hours, base_price, currency)
# base_price = regular/economic class price for the route
# All routes accept any bus type (Cargo Cruisers pass through Kampala on the Juba–Nairobi corridor)
ROUTES = [
    # Kampala ↔ Nairobi
    ("KLA", "NBO", Decimal("660"),  12, Decimal("120000"), "UGX"),
    ("NBO", "KLA", Decimal("660"),  12, Decimal("3500"),   "KES"),
    # Nairobi ↔ Juba (via Kampala)
    ("NBO", "JUB", Decimal("1200"), 22, Decimal("10000"),  "KES"),
    ("JUB", "NBO", Decimal("1200"), 22, Decimal("10000"),  "KES"),
    # Nairobi ↔ Kakuma
    ("NBO", "KAK", Decimal("740"),  12, Decimal("4000"),   "KES"),
    ("KAK", "NBO", Decimal("740"),  12, Decimal("4000"),   "KES"),
    # Nairobi ↔ Kigali
    ("NBO", "KGL", Decimal("1400"), 20, Decimal("7000"),   "KES"),
    ("KGL", "NBO", Decimal("1400"), 20, Decimal("80000"),  "RWF"),
    # Kigali ↔ Kampala
    ("KGL", "KLA", Decimal("510"),  10, Decimal("140000"), "UGX"),
    ("KLA", "KGL", Decimal("510"),  10, Decimal("140000"), "UGX"),
    # Juba ↔ Kampala
    ("JUB", "KLA", Decimal("640"),  12, Decimal("250000"), "SSP"),
    ("KLA", "JUB", Decimal("640"),  12, Decimal("250000"), "SSP"),
]


class Command(BaseCommand):
    help = "Seed EcoBus cities and routes with real pricing"

    def add_arguments(self, parser):
        parser.add_argument("--clear", action="store_true", help="Delete existing routes and cities first")

    def handle(self, *args, **options):
        if options["clear"]:
            self.stdout.write("Clearing existing routes & cities...")
            Route.objects.all().delete()
            City.objects.all().delete()

        # --- Cities ---
        city_map = {}
        for c in CITIES:
            city, created = City.objects.get_or_create(
                code=c["code"],
                defaults={"name": c["name"], "country": c["country"]},
            )
            city_map[c["code"]] = city
            status = "created" if created else "exists"
            self.stdout.write(f"  City {city.name} ({city.code}) — {status}")

        # --- Routes ---
        created_count = 0
        for origin_code, dest_code, dist, hours, price, currency in ROUTES:
            origin = city_map[origin_code]
            dest = city_map[dest_code]
            route, created = Route.objects.get_or_create(
                origin=origin,
                destination=dest,
                defaults={
                    "distance_km": dist,
                    "estimated_duration": timedelta(hours=hours),
                    "base_price": price,
                    "vip_price": (price * Decimal("1.20")).quantize(Decimal("0.01")),
                    "currency": currency,
                    "allowed_bus_types": [],
                },
            )
            if not created:
                # Clear any previously-set restrictions
                route.allowed_bus_types = []
                route.save(update_fields=["allowed_bus_types", "updated_at"])
            else:
                created_count += 1
            status = "created" if created else "updated"
            self.stdout.write(
                f"  Route {origin.name} → {dest.name}  "
                f"({dist} km, {hours}h, {price} {currency}) — {status}"
            )

        self.stdout.write(self.style.SUCCESS(
            f"\nDone! {City.objects.count()} cities, {Route.objects.count()} routes "
            f"({created_count} new)."
        ))
