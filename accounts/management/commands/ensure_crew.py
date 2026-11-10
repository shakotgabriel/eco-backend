"""Create missing DRIVER and OPERATOR demo accounts without touching passwords.

Safe to run on a populated production database. Unlike seed_demo_data, this
does not reset existing user passwords and does not rewrite STAFF accounts.
"""
from django.core.management.base import BaseCommand

from accounts.models import User

DEMO_PASSWORD = "Demo@12345"

# username, email, first, last, role, phone
CREW = (
    ("driver.nairobi", "driver.nairobi@ecobus.co.ss", "Daniel", "Mwangi", "DRIVER", "+254712000004"),
    ("driver.kampala", "driver.kampala@ecobus.co.ss", "Joseph", "Ssebunya", "DRIVER", "+256772000004"),
    ("driver.juba", "driver.juba@ecobus.co.ss", "Michael", "Deng", "DRIVER", "+211920000004"),
    ("driver.kigali", "driver.kigali@ecobus.co.ss", "Eric", "Habimana", "DRIVER", "+250788000003"),
    ("operator.nairobi", "operator.nairobi@ecobus.co.ss", "Lucy", "Njeri", "OPERATOR", "+254712000005"),
    ("operator.kampala", "operator.kampala@ecobus.co.ss", "Agnes", "Namutebi", "OPERATOR", "+256772000005"),
    ("operator.juba", "operator.juba@ecobus.co.ss", "Rebecca", "Akol", "OPERATOR", "+211920000005"),
    ("operator.kigali", "operator.kigali@ecobus.co.ss", "Chantal", "Uwera", "OPERATOR", "+250788000004"),
)


class Command(BaseCommand):
    help = "Get-or-create DRIVER and OPERATOR users. Does not reset existing passwords."

    def handle(self, *args, **options):
        created_count = 0
        existing_count = 0
        for username, email, first, last, role, phone in CREW:
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    "email": email,
                    "first_name": first,
                    "last_name": last,
                    "role": role,
                    "phone_number": phone,
                    "is_staff": False,
                    "is_superuser": False,
                },
            )
            if created:
                user.set_password(DEMO_PASSWORD)
                user.save(update_fields=["password"])
                created_count += 1
                self.stdout.write(f"  Created {username} ({role})")
                continue

            changed = []
            if user.role != role:
                user.role = role
                changed.append("role")
            if user.email != email:
                user.email = email
                changed.append("email")
            if not user.first_name:
                user.first_name = first
                changed.append("first_name")
            if not user.last_name:
                user.last_name = last
                changed.append("last_name")
            if not user.phone_number:
                user.phone_number = phone
                changed.append("phone_number")
            if changed:
                user.save(update_fields=changed)
                self.stdout.write(f"  Updated {username} ({', '.join(changed)})")
            else:
                existing_count += 1

        self.stdout.write(self.style.SUCCESS(
            f"Crew accounts: {created_count} created, {existing_count} already present."
        ))
