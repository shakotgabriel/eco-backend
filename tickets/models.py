import uuid

from bebeto_backend.base import BaseModel
from django.core.exceptions import ValidationError
from django.db import models
from phonenumber_field.modelfields import PhoneNumberField
from django.utils import timezone


class Ticket(BaseModel):
    STATUS_CHOICES = (
        ("VALID", "Valid"),
        ("USED", "Used"),
        ("CANCELLED", "Cancelled"),
    )

    ticket_number = models.CharField(max_length=30, unique=True, editable=False)
    booking = models.ForeignKey(
        "bookings.Booking", on_delete=models.CASCADE, related_name="tickets"
    )
    seat = models.ForeignKey(
        "seats.Seat", on_delete=models.SET_NULL, null=True, related_name="tickets"
    )

    # Passenger details (one per ticket / seat)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    phone_number = PhoneNumberField(help_text="e.g. +211722000000")
    id_number = models.CharField(max_length=50, help_text="National ID or passport number")
    nationality = models.CharField(max_length=100, blank=True)

    seat_class = models.CharField(
        max_length=10,
        choices=[("VIP", "VIP"), ("REGULAR", "Regular")],
        default="REGULAR",
    )
    price = models.DecimalField(max_digits=10, decimal_places=2, help_text="Price for this ticket")
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default="VALID")

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["booking", "status"], name="idx_ticket_booking_status"),
            models.Index(fields=["status"], name="idx_ticket_status"),
        ]

    def clean(self):
        if self.seat_id and self.booking_id:
            trip_bus = self.booking.trip.bus
            if self.seat.bus_id != trip_bus.id:
                raise ValidationError("The selected seat does not belong to this trip's bus.")

    def save(self, *args, **kwargs):
        if not self.ticket_number:
            date_str = timezone.now().strftime("%Y%m%d")
            unique_id = uuid.uuid4().hex[:8].upper()
            self.ticket_number = f"TKT-{date_str}-{unique_id}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Ticket {self.ticket_number} - {self.first_name} {self.last_name}"
