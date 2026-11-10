from bebeto_backend.base import BaseModel
from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

SEAT_LAYOUT_VALIDATOR = RegexValidator(
    regex=r"^[1-4]\+[1-4]$",
    message="Use N+M with 1–4 seats on each side of the aisle, e.g. 1+2 or 2+3.",
)


class Bus(BaseModel):
    BUS_TYPE_CHOICES = (
        ("EXECUTIVE_CRUISER", "Executive Cruiser (VIP)"),
        ("CARGO_CRUISER", "Cargo Cruiser"),
        ("COMFORT_CRUISER", "Comfort Cruiser"),
    )
    STATUS_CHOICES = (
        ("ACTIVE", "Active"),
        ("MAINTENANCE", "Under Maintenance"),
        ("RETIRED", "Retired"),
    )
    operator = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="operated_buses",
        help_text="Operator who manages this bus (not the trip driver)",
        limit_choices_to={"role": "OPERATOR"},
    )
    plate_number = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=100, blank=True)
    home_city = models.ForeignKey(
        "cities.City", on_delete=models.SET_NULL, null=True, blank=True, related_name="buses"
    )
    capacity = models.PositiveIntegerField(help_text="Total passenger seat capacity")
    bus_type = models.CharField(max_length=20, choices=BUS_TYPE_CHOICES, default="COMFORT_CRUISER")
    seat_layout = models.CharField(
        max_length=5,
        default="2+2",
        validators=[SEAT_LAYOUT_VALIDATOR],
        help_text="Seats left of the aisle + seats right of the aisle, e.g. 2+1 or 1+2.",
    )
    total_rows = models.PositiveIntegerField(default=10, help_text="Number of seat rows")
    has_cargo_section = models.BooleanField(default=False, help_text="Whether this bus has a dedicated cargo area")
    vip_seats = models.PositiveIntegerField(default=0, help_text="Number of VIP seats on this bus")
    regular_seats = models.PositiveIntegerField(default=0, help_text="Number of regular seats on this bus")
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default="ACTIVE")
    current_mileage_km = models.PositiveIntegerField(
        default=0, help_text="Current odometer reading in km"
    )
    insurance_expiry = models.DateField(null=True, blank=True, help_text="Date the insurance cover ends")
    road_licence_expiry = models.DateField(null=True, blank=True, help_text="Date the road licence ends")
    inspection_expiry = models.DateField(null=True, blank=True, help_text="Date the roadworthiness inspection ends")

    class Meta:
        verbose_name_plural = "buses"
        ordering = ["name"]

    def __str__(self):
        return f"{self.name or self.plate_number} ({self.bus_type})"

    @property
    def is_overdue_for_service(self):
        """Check if the bus has any overdue maintenance."""
        now = timezone.now().date()
        return self.maintenance_records.filter(
            next_service_date__lt=now,
            next_service_date__isnull=False,
        ).exists()

    @property
    def next_service_due(self):
        """Return the earliest upcoming service date."""
        record = (
            self.maintenance_records
            .filter(next_service_date__isnull=False, next_service_date__gte=timezone.now().date())
            .order_by("next_service_date")
            .first()
        )
        return record.next_service_date if record else None


class MaintenanceRecord(BaseModel):
    """Tracks service/repair history for a bus."""
    TYPE_CHOICES = (
        ("OIL_CHANGE", "Oil Change"),
        ("TIRE_REPLACEMENT", "Tire Replacement"),
        ("BRAKE_SERVICE", "Brake Service"),
        ("ENGINE_REPAIR", "Engine Repair"),
        ("AC_SERVICE", "AC / Heating Service"),
        ("BODY_REPAIR", "Body Repair"),
        ("ELECTRICAL", "Electrical System"),
        ("INSPECTION", "General Inspection"),
        ("FULL_SERVICE", "Full Service"),
        ("OTHER", "Other"),
    )
    STATUS_CHOICES = (
        ("SCHEDULED", "Scheduled"),
        ("IN_PROGRESS", "In Progress"),
        ("COMPLETED", "Completed"),
        ("CANCELLED", "Cancelled"),
    )

    bus = models.ForeignKey(
        Bus, on_delete=models.CASCADE, related_name="maintenance_records"
    )
    maintenance_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default="SCHEDULED")
    description = models.TextField(blank=True, help_text="Details of work performed or needed")

    # Scheduling
    scheduled_date = models.DateField(help_text="When this maintenance is/was scheduled")
    completed_date = models.DateField(null=True, blank=True, help_text="When this was actually completed")

    # Cost
    cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, default="SSP")
    vendor = models.CharField(max_length=200, blank=True, help_text="Service provider / garage name")

    # Mileage
    mileage_at_service = models.PositiveIntegerField(
        null=True, blank=True, help_text="Odometer reading at time of service"
    )

    # Next service
    next_service_date = models.DateField(
        null=True, blank=True, help_text="When next service of this type is due"
    )
    next_service_mileage = models.PositiveIntegerField(
        null=True, blank=True, help_text="Mileage at which next service is due"
    )

    # Who
    performed_by = models.CharField(max_length=200, blank=True, help_text="Mechanic / technician name")
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="approved_maintenance",
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-scheduled_date"]

    def __str__(self):
        return f"{self.bus.name or self.bus.plate_number} — {self.get_maintenance_type_display()} ({self.scheduled_date})"
