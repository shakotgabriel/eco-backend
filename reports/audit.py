"""
Lightweight audit log for critical business actions.
Tracks who did what to which object and when.
"""
import logging

from django.conf import settings
from django.db import models

logger = logging.getLogger(__name__)


class AuditLog(models.Model):
    """Records critical actions for dispute resolution and compliance."""
    ACTION_CHOICES = (
        ("BOOKING_CREATED", "Booking Created"),
        ("BOOKING_CANCELLED", "Booking Cancelled"),
        ("BOOKING_RESCHEDULED", "Booking Rescheduled"),
        ("PAYMENT_COMPLETED", "Payment Completed"),
        ("PAYMENT_FAILED", "Payment Failed"),
        ("PAYMENT_REFUNDED", "Payment Refunded"),
        ("TRIP_CANCELLED", "Trip Cancelled"),
        ("TRIP_STATUS_CHANGED", "Trip Status Changed"),
        ("TICKET_USED", "Ticket Used (Boarding)"),
        ("AGENT_COMMISSION_PAID", "Agent Commission Paid"),
        ("USER_ROLE_CHANGED", "User Role Changed"),
    )

    action = models.CharField(max_length=30, choices=ACTION_CHOICES)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="audit_logs",
        help_text="User who performed the action (null for system actions)",
    )
    target_model = models.CharField(max_length=50, help_text="e.g. bookings.Booking")
    target_id = models.CharField(max_length=100, help_text="PK of the target object")
    details = models.JSONField(default=dict, blank=True, help_text="Additional context")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["action", "created_at"]),
            models.Index(fields=["target_model", "target_id"]),
        ]

    def __str__(self):
        return f"{self.action} on {self.target_model}#{self.target_id} by {self.actor} at {self.created_at}"


def log_action(action, target_obj=None, actor=None, request=None, details=None):
    """
    Helper to create audit log entries.

    Usage:
        from reports.audit import log_action
        log_action("BOOKING_CANCELLED", booking, request=request, details={"reason": "..."})
    """
    target_model = ""
    target_id = ""
    if target_obj:
        target_model = f"{target_obj.__class__._meta.app_label}.{target_obj.__class__.__name__}"
        target_id = str(target_obj.pk)

    ip = None
    if request:
        ip = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip() or request.META.get("REMOTE_ADDR")
        if not actor and request.user and request.user.is_authenticated:
            actor = request.user

    try:
        AuditLog.objects.create(
            action=action,
            actor=actor,
            target_model=target_model,
            target_id=target_id,
            details=details or {},
            ip_address=ip,
        )
    except Exception as exc:
        logger.warning(f"Failed to create audit log: {exc}")
