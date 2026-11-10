"""Create the first portal ADMIN from DJANGO_SUPERUSER_* env vars.

Idempotent: does not reset the password on later boots (Render free
restarts and sleeps often). Only creates when the username is missing,
and upgrades an existing user to ADMIN/staff if needed.
"""
import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

User = get_user_model()


class Command(BaseCommand):
    help = "Ensure an ADMIN exists from DJANGO_SUPERUSER_USERNAME/EMAIL/PASSWORD"

    def handle(self, *args, **options):
        username = os.environ.get("DJANGO_SUPERUSER_USERNAME", "").strip()
        email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "").strip()
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD", "")

        if not username or not password:
            self.stdout.write(
                "DJANGO_SUPERUSER_USERNAME or DJANGO_SUPERUSER_PASSWORD not set; skip."
            )
            return

        defaults = {
            "email": email or f"{username}@ecobus.co.ss",
            "role": "ADMIN",
            "is_staff": True,
            "is_superuser": True,
            "is_active": True,
        }
        user, created = User.objects.get_or_create(username=username, defaults=defaults)
        if created:
            user.set_password(password)
            user.save(update_fields=["password"])
            self.stdout.write(self.style.SUCCESS(f"Created ADMIN '{username}'."))
            return

        changed_fields = []
        if user.role != "ADMIN":
            user.role = "ADMIN"
            changed_fields.append("role")
        if not user.is_staff:
            user.is_staff = True
            changed_fields.append("is_staff")
        if not user.is_superuser:
            user.is_superuser = True
            changed_fields.append("is_superuser")
        if not user.is_active:
            user.is_active = True
            changed_fields.append("is_active")
        if email and user.email != email:
            user.email = email
            changed_fields.append("email")
        if changed_fields:
            user.save(update_fields=changed_fields)
            self.stdout.write(f"Updated '{username}' ({', '.join(changed_fields)}).")
        else:
            self.stdout.write(f"ADMIN '{username}' already exists.")
