"""Seed catalog data when the database has no cities.

Safe for Render free tier: no Shell required. Catalog seed is a no-op after
the first successful run. Driver/operator accounts are always ensured.
"""
from django.core.management import call_command
from django.core.management.base import BaseCommand

from cities.models import City


class Command(BaseCommand):
    help = "Run seed_routes, seed_fleet, seed_schedules, and seed_demo_data if the DB is empty"

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Seed even if cities already exist (schedules/fleet remain idempotent)",
        )

    def handle(self, *args, **options):
        if City.objects.exists() and not options["force"]:
            self.stdout.write("Catalog already seeded (cities exist); skip catalog seed.")
            # Drivers/operators were added after the first seed. Always ensure they exist.
            call_command("ensure_crew")
            return

        self.stdout.write("Empty catalog — seeding cities, routes, fleet, schedules, demo users...")
        call_command("seed_routes")
        call_command("seed_fleet")
        call_command("seed_schedules")
        call_command("seed_demo_data")
        call_command("ensure_crew")

        from trips.tasks import generate_scheduled_trips

        result = generate_scheduled_trips(days_ahead=7)
        self.stdout.write(self.style.SUCCESS(f"Generated trips: {result}"))
