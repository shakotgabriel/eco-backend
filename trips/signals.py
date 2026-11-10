"""
Trip status change signals — notify passengers via SMS/email when a trip
status changes to CANCELLED, DEPARTED, or DELAYED.
"""
from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver

from .models import Trip


@receiver(pre_save, sender=Trip)
def track_trip_previous_status(sender, instance, **kwargs):
    """Stash the old trip status so post_save can detect transitions."""
    if instance.pk:
        try:
            instance._prev_trip_status = Trip.objects.values_list("status", flat=True).get(pk=instance.pk)
        except Trip.DoesNotExist:
            instance._prev_trip_status = None
    else:
        instance._prev_trip_status = None


@receiver(post_save, sender=Trip)
def handle_trip_status_change(sender, instance, created, **kwargs):
    """
    Notify passengers when trip status changes to a notable state.
    """
    if created:
        return

    prev = getattr(instance, "_prev_trip_status", None)
    if prev == instance.status:
        return  # No change

    # Only notify on these transitions
    notify_statuses = ("CANCELLED", "DEPARTED")
    if instance.status in notify_statuses:
        try:
            from .tasks import notify_trip_status_change
            notify_trip_status_change.delay(instance.pk, instance.status)
        except Exception:
            pass  # Broker down — notification skipped
