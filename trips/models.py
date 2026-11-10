from bebeto_backend.base import BaseModel
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class Trip(BaseModel):
    STATUS_CHOICES = (
        ("SCHEDULED", "Scheduled"),
        ("BOARDING", "Boarding"),
        ("DEPARTED", "Departed"),
        ("ARRIVED", "Arrived"),
        ("COMPLETED", "Completed"),
        ("CANCELLED", "Cancelled"),
    )

    route = models.ForeignKey(
        "routes.Route", on_delete=models.CASCADE, related_name="trips"
    )
    bus = models.ForeignKey(
        "buses.Bus", on_delete=models.CASCADE, related_name="trips"
    )
    driver = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="driven_trips",
        help_text="Driver assigned to this trip",
        limit_choices_to={"role": "DRIVER"},
    )
    departure_time = models.DateTimeField()
    arrival_time = models.DateTimeField()
    regular_price = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="Price per regular seat")
    vip_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text="Price per VIP seat (null if bus has no VIP seats)")
    cargo_price_per_kg = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text="Cargo rate per kg (for Cargo Cruisers)")
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default="SCHEDULED")
    available_seats = models.PositiveIntegerField()

    class Meta:
        ordering = ["departure_time"]
        indexes = [
            models.Index(fields=["status", "departure_time"], name="idx_trip_status_departure"),
            models.Index(fields=["route", "status", "departure_time"], name="idx_trip_route_status_dep"),
        ]

    def clean(self):
        if self.arrival_time and self.departure_time and self.arrival_time <= self.departure_time:
            raise ValidationError("Arrival time must be after departure time.")

        if self.bus_id and self.route_id:
            self.route.validate_bus(self.bus)

        if self.bus_id and self.departure_time and self.arrival_time:
            overlapping = Trip.objects.filter(
                bus=self.bus,
                status__in=["SCHEDULED", "BOARDING", "DEPARTED"],
            ).filter(
                Q(departure_time__lt=self.arrival_time) & Q(arrival_time__gt=self.departure_time)
            )
            if self.pk:
                overlapping = overlapping.exclude(pk=self.pk)
            if overlapping.exists():
                raise ValidationError("This bus is already assigned to another trip during this time.")

    def __str__(self):
        return f"{self.route} | {self.departure_time:%Y-%m-%d %H:%M}"


class TripSchedule(BaseModel):
    """
    Recurring timetable template. Managers generate Trip instances from
    active schedules via admin (Generate week/month).
    """
    DAY_CHOICES = (
        (0, "Monday"),
        (1, "Tuesday"),
        (2, "Wednesday"),
        (3, "Thursday"),
        (4, "Friday"),
        (5, "Saturday"),
        (6, "Sunday"),
    )

    route = models.ForeignKey(
        "routes.Route", on_delete=models.CASCADE, related_name="schedules"
    )
    bus = models.ForeignKey(
        "buses.Bus", on_delete=models.CASCADE, related_name="schedules"
    )
    driver = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="scheduled_drives",
        help_text="Default driver for trips generated from this schedule",
        limit_choices_to={"role": "DRIVER"},
    )
    departure_time = models.TimeField(help_text="Departure time of day (e.g. 06:00)")
    days_of_week = models.JSONField(
        default=list,
        help_text="List of day numbers: 0=Mon … 6=Sun. E.g. [0,1,2,3,4,5,6] for daily."
    )
    regular_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    vip_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    cargo_price_per_kg = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["route", "departure_time"]
        verbose_name = "trip schedule"

    def clean(self):
        if self.bus_id and self.route_id:
            self.route.validate_bus(self.bus)

    def __str__(self):
        days = ", ".join(dict(self.DAY_CHOICES).get(d, "?") for d in (self.days_of_week or []))
        return f"{self.route} @ {self.departure_time:%H:%M} [{days}]"
