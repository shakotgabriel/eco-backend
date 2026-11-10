"""
Django signals to trigger fraud detection on booking/payment events.
"""
import logging

from django.db.models.signals import post_save
from django.dispatch import receiver
from bookings.models import Booking
from payments.models import Payment
from fraud_detection.tasks import check_booking_fraud, check_payment_fraud

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Booking)
def on_booking_created_or_updated(sender, instance, created, **kwargs):
    """Trigger fraud checks when booking is created or updated."""
    if created and instance.status in ['PENDING', 'CONFIRMED']:
        try:
            check_booking_fraud.delay(instance.id)
        except Exception:
            # Broker unavailable — never let fraud-check dispatch break booking creation.
            logger.exception("Failed to queue check_booking_fraud for booking %s", instance.id)


@receiver(post_save, sender=Payment)
def on_payment_created_or_updated(sender, instance, created, **kwargs):
    """Trigger fraud checks when payment is created or updated."""
    if created or instance.status in ['COMPLETED', 'FAILED']:
        try:
            check_payment_fraud.delay(instance.id)
        except Exception:
            logger.exception("Failed to queue check_payment_fraud for payment %s", instance.id)
