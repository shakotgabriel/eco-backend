"""
Management command: cleanup_stale_payments

Run periodically (via Celery beat or cron):
  python manage.py cleanup_stale_payments

Marks payments as FAILED if they've been PENDING for too long.
Cancels associated bookings and releases seats.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.conf import settings
from django.db import transaction


class Command(BaseCommand):
    help = "Mark stale PENDING payments as FAILED and cancel associated bookings"

    def add_arguments(self, parser):
        parser.add_argument(
            "--timeout-minutes",
            type=int,
            default=30,
            help="Mark payments as FAILED if PENDING for this many minutes (default: 30)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be done without actually saving",
        )

    def handle(self, *args, **options):
        timeout_minutes = options["timeout_minutes"]
        dry_run = options["dry_run"]
        
        from payments.models import Payment
        from bookings.models import Booking

        cutoff_time = timezone.now() - timedelta(minutes=timeout_minutes)
        
        # Find all PENDING payments older than timeout
        stale_payments = Payment.objects.filter(
            status="PENDING",
            created_at__lt=cutoff_time,
        ).select_related("booking")

        count = 0
        for payment in stale_payments:
            booking = payment.booking
            
            if dry_run:
                self.stdout.write(
                    f"[DRY RUN] Would fail payment {payment.id} "
                    f"(booking {booking.booking_reference}, created {payment.created_at})"
                )
                continue

            with transaction.atomic():
                # Mark payment as FAILED
                payment.status = "FAILED"
                payment.save(update_fields=["status", "updated_at"])

                # Cancel booking + restore seats (signal handles this)
                if booking.status == "PENDING":
                    booking.status = "CANCELLED"
                    booking.save()
                    
                    self.stdout.write(
                        self.style.WARNING(
                            f"✓ Cancelled payment {payment.id} and booking "
                            f"{booking.booking_reference} (PENDING for {timeout_minutes}+ min)"
                        )
                    )
                    count += 1
                    
                    # Send SMS notification to customer
                    try:
                        from bookings.tasks import send_booking_cancellation_sms
                        send_booking_cancellation_sms.delay(booking.pk)
                    except Exception as e:
                        self.stderr.write(f"Failed to send SMS for {booking.booking_reference}: {e}")

        self.stdout.write(
            self.style.SUCCESS(f"\n✓ Cleanup complete. Processed {count} stale bookings.")
        )
