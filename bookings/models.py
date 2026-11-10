import uuid

from bebeto_backend.base import BaseModel
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from phonenumber_field.modelfields import PhoneNumberField
from django.utils import timezone


class BookingGroup(BaseModel):
    """Groups multiple bookings together for round-trip or multi-leg journeys."""
    TRIP_TYPE_CHOICES = (
        ("ONE_WAY", "One Way"),
        ("RETURN", "Return Trip"),
        ("MULTI_LEG", "Multi-leg"),
    )

    group_reference = models.CharField(max_length=25, unique=True, editable=False)
    trip_type = models.CharField(max_length=10, choices=TRIP_TYPE_CHOICES, default="ONE_WAY")
    discount_pct = models.DecimalField(max_digits=5, decimal_places=2, default=0, help_text="Discount % for return/multi-leg")

    def save(self, *args, **kwargs):
        if not self.group_reference:
            date_str = timezone.now().strftime("%Y%m%d")
            unique_id = uuid.uuid4().hex[:6].upper()
            self.group_reference = f"GRP-{date_str}-{unique_id}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.group_reference} ({self.trip_type})"


class Booking(BaseModel):
    STATUS_CHOICES = (
        ("PENDING", "Pending"),
        ("CONFIRMED", "Confirmed"),
        ("CANCELLED", "Cancelled"),
        ("RESCHEDULED", "Rescheduled"),
        ("COMPLETED", "Completed"),
    )

    booking_reference = models.CharField(max_length=20, unique=True, editable=False)
    group = models.ForeignKey(
        BookingGroup, on_delete=models.SET_NULL, null=True, blank=True, related_name="bookings",
        help_text="Links bookings in a round-trip or multi-leg journey",
    )
    leg_order = models.PositiveIntegerField(default=1, help_text="Order of this leg in a multi-leg journey")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bookings",
    )
    trip = models.ForeignKey(
        "trips.Trip", on_delete=models.CASCADE, related_name="bookings"
    )

    # Payer / payment contact details
    payer_name = models.CharField(max_length=200, help_text="Full name of the person paying")
    payment_phone = PhoneNumberField(db_index=True, help_text="Mobile money number for payment")
    email = models.EmailField(blank=True, help_text="Optional — ticket confirmation will be sent here")

    number_of_seats = models.PositiveIntegerField(default=1)
    total_price = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default="PENDING")
    expires_at = models.DateTimeField(null=True, blank=True, help_text="Auto-cancel if still PENDING after this time")

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "expires_at"], name="idx_booking_status_expires"),
            models.Index(fields=["trip", "status"], name="idx_booking_trip_status"),
        ]

    def clean(self):
        if self.trip_id and self.number_of_seats:
            if self.number_of_seats > self.trip.available_seats:
                raise ValidationError(
                    f"Only {self.trip.available_seats} seat(s) available, but {self.number_of_seats} requested."
                )

    def save(self, *args, **kwargs):
        if not self.booking_reference:
            date_str = timezone.now().strftime("%Y%m%d")
            unique_id = uuid.uuid4().hex[:6].upper()
            self.booking_reference = f"BEB-{date_str}-{unique_id}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.booking_reference} - {self.payer_name}"


class Reschedule(BaseModel):
    """Tracks a reschedule: original booking cancelled, new booking created with fare difference."""
    STATUS_CHOICES = (
        ("PENDING_PAYMENT", "Pending Additional Payment"),
        ("COMPLETED", "Completed"),
        ("REFUND_PENDING", "Refund Pending"),
        ("REFUND_ISSUED", "Refund Issued"),
    )

    original_booking = models.ForeignKey(
        Booking, on_delete=models.CASCADE, related_name="rescheduled_from",
        help_text="The original booking being rescheduled",
    )
    new_booking = models.ForeignKey(
        Booking, on_delete=models.CASCADE, related_name="rescheduled_to",
        help_text="The new replacement booking",
    )
    original_fare = models.DecimalField(max_digits=10, decimal_places=2)
    new_fare = models.DecimalField(max_digits=10, decimal_places=2)
    fare_difference = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text="Positive = passenger owes more; negative = refund due",
    )
    reschedule_fee = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text="Flat reschedule fee charged to the passenger",
    )
    total_due = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text="fare_difference + reschedule_fee (amount passenger must pay or be refunded)",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="COMPLETED")
    reason = models.TextField(blank=True, help_text="Reason for reschedule")
    rescheduled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        help_text="Staff/agent who processed the reschedule",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Reschedule {self.original_booking.booking_reference} → {self.new_booking.booking_reference}"
