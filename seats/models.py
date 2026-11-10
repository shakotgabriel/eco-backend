from bebeto_backend.base import BaseModel
from django.db import models
from django.utils import timezone


class Seat(BaseModel):
    SEAT_TYPE_CHOICES = (
        ("VIP", "VIP"),
        ("REGULAR", "Regular"),
    )
    POSITION_CHOICES = (
        ("WINDOW", "Window"),
        ("AISLE", "Aisle"),
        ("MIDDLE", "Middle"),
    )

    bus = models.ForeignKey(
        "buses.Bus", on_delete=models.CASCADE, related_name="seats"
    )
    seat_number = models.CharField(max_length=5)
    row = models.PositiveIntegerField(default=1, help_text="Row number (1-indexed)")
    column = models.CharField(max_length=1, default="A", help_text="Column letter (A, B, C, D)")
    position = models.CharField(max_length=10, choices=POSITION_CHOICES, default="AISLE")
    seat_type = models.CharField(max_length=10, choices=SEAT_TYPE_CHOICES, default="REGULAR")
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["bus", "seat_number"], name="unique_seat_per_bus"),
            models.UniqueConstraint(fields=["bus", "row", "column"], name="unique_seat_position"),
        ]
        ordering = ["bus", "row", "column"]

    def __str__(self):
        return f"Seat {self.seat_number} ({self.row}{self.column}) - {self.bus}"


class SeatLock(models.Model):
    """Temporary seat lock during booking — prevents double-booking the same seat."""
    seat = models.ForeignKey(Seat, on_delete=models.CASCADE, related_name="locks")
    trip = models.ForeignKey("trips.Trip", on_delete=models.CASCADE, related_name="seat_locks")
    session_key = models.CharField(max_length=64, help_text="Browser session or booking session ID")
    locked_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["seat", "trip"], name="unique_seat_lock_per_trip"),
        ]
        indexes = [
            models.Index(fields=["trip", "expires_at"], name="idx_seatlock_trip_expires"),
        ]

    @property
    def is_expired(self):
        return timezone.now() > self.expires_at

    def __str__(self):
        return f"Lock: Seat {self.seat.seat_number} on Trip {self.trip_id} until {self.expires_at}"
