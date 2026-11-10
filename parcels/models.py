import secrets
import uuid

from bebeto_backend.base import BaseModel
from django.db import models
from phonenumber_field.modelfields import PhoneNumberField
from django.utils import timezone


class Parcel(BaseModel):
    """A parcel or extra-luggage shipment on a bus trip."""
    STATUS_CHOICES = (
        ("PENDING", "Pending Pickup"),
        ("IN_TRANSIT", "In Transit"),
        ("ARRIVED", "Arrived at Destination"),
        ("DELIVERED", "Delivered"),
        ("CANCELLED", "Cancelled"),
    )
    SIZE_CHOICES = (
        ("SMALL", "Small (≤5kg)"),
        ("MEDIUM", "Medium (5-15kg)"),
        ("LARGE", "Large (15-30kg)"),
        ("OVERSIZED", "Oversized (>30kg)"),
    )

    tracking_number = models.CharField(max_length=25, unique=True, editable=False)
    trip = models.ForeignKey(
        "trips.Trip", on_delete=models.CASCADE, related_name="parcels"
    )

    # Sender
    sender_name = models.CharField(max_length=200)
    sender_phone = PhoneNumberField()

    # Receiver
    receiver_name = models.CharField(max_length=200)
    receiver_phone = PhoneNumberField()

    # Parcel details
    description = models.CharField(max_length=500, help_text="Brief description of contents")
    weight_kg = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    size = models.CharField(max_length=10, choices=SIZE_CHOICES, default="SMALL")
    declared_value = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text="Declared value for insurance")

    # Pricing
    shipping_fee = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default="SSP")

    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default="PENDING")
    notes = models.TextField(blank=True)

    # Pickup confirmation — 6-digit code SMS'd to receiver on ARRIVED
    pickup_code = models.CharField(
        max_length=6, blank=True,
        help_text="6-digit code sent to receiver for delivery confirmation",
    )
    picked_up_at = models.DateTimeField(null=True, blank=True)

    # Payment link (for digital payment of shipping fee)
    payment = models.ForeignKey(
        "payments.Payment", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="parcels",
        help_text="Digital payment for shipping fee",
    )

    # Optional link to a booking (if luggage for a passenger)
    booking = models.ForeignKey(
        "bookings.Booking", on_delete=models.SET_NULL, null=True, blank=True, related_name="parcels"
    )

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if not self.tracking_number:
            date_str = timezone.now().strftime("%Y%m%d")
            unique_id = uuid.uuid4().hex[:6].upper()
            self.tracking_number = f"PCL-{date_str}-{unique_id}"
        super().save(*args, **kwargs)

    def generate_pickup_code(self):
        """Generate a 6-digit numeric pickup code."""
        self.pickup_code = f"{secrets.randbelow(900000) + 100000}"
        self.save(update_fields=["pickup_code"])
        return self.pickup_code

    def __str__(self):
        return f"{self.tracking_number} — {self.sender_name} → {self.receiver_name}"


class ParcelPhoto(BaseModel):
    """Staff-uploaded picture of a parcel (intake, damage, handover)."""
    parcel = models.ForeignKey(Parcel, on_delete=models.CASCADE, related_name="photos")
    image = models.ImageField(upload_to="parcels/%Y/%m/")
    caption = models.CharField(max_length=200, blank=True)
    uploaded_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="parcel_photos",
    )

    class Meta:
        ordering = ["created_at"]

    def delete(self, *args, **kwargs):
        image_name = self.image.name if self.image else ""
        storage = self.image.storage if self.image else None
        super().delete(*args, **kwargs)
        if image_name and storage:
            storage.delete(image_name)

    def __str__(self):
        return f"{self.parcel.tracking_number} photo #{self.pk}"


class ParcelStatusLog(models.Model):
    """Tracks status changes for a parcel (audit trail)."""
    parcel = models.ForeignKey(Parcel, on_delete=models.CASCADE, related_name="status_logs")
    old_status = models.CharField(max_length=15, blank=True)
    new_status = models.CharField(max_length=15)
    changed_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True
    )
    location = models.ForeignKey(
        "cities.City", on_delete=models.SET_NULL, null=True, blank=True,
        help_text="City/station where this status change occurred",
    )
    note = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.parcel.tracking_number}: {self.old_status} → {self.new_status}"
