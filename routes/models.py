from bebeto_backend.base import BaseModel
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class Route(BaseModel):
    CURRENCY_CHOICES = (
        ("SSP", "South Sudanese Pound"),
        ("KES", "Kenyan Shilling"),
        ("UGX", "Ugandan Shilling"),
        ("RWF", "Rwandan Franc"),
        ("USD", "US Dollar"),
    )

    origin = models.ForeignKey(
        "cities.City", on_delete=models.CASCADE, related_name="routes_from"
    )
    destination = models.ForeignKey(
        "cities.City", on_delete=models.CASCADE, related_name="routes_to"
    )
    distance_km = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    estimated_duration = models.DurationField(help_text="Estimated travel time")
    base_price = models.DecimalField(max_digits=10, decimal_places=2, help_text="Standard / regular seat fare")
    vip_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="VIP seat fare for this corridor. Null = not set (schedules can still override).",
    )
    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES, default="SSP")
    allowed_bus_types = models.JSONField(
        default=list,
        help_text="Bus types allowed on this route, e.g. ['EXECUTIVE_CRUISER','COMFORT_CRUISER']. Empty = any."
    )
    assigned_buses = models.ManyToManyField(
        "buses.Bus",
        blank=True,
        related_name="assigned_routes",
        help_text="Specific fleet buses that run this route. Empty = any bus of the allowed types.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["origin", "destination"], name="unique_route"),
        ]
        ordering = ["origin__name", "destination__name"]

    def clean(self):
        if self.origin_id and self.destination_id and self.origin_id == self.destination_id:
            raise ValidationError("Origin and destination cannot be the same city.")

    def validate_bus(self, bus):
        """Reject a fleet bus that is not permitted on this route."""
        if not bus:
            return
        if self.pk:
            assigned_ids = list(self.assigned_buses.values_list("pk", flat=True))
            if assigned_ids and bus.pk not in assigned_ids:
                raise ValidationError(
                    f"{bus.plate_number} is not assigned to {self}. "
                    "Pick a bus from this route's fleet, or clear assigned buses "
                    "on the route to allow any matching type."
                )
        allowed = self.allowed_bus_types
        if allowed and bus.bus_type not in allowed:
            raise ValidationError(
                f"{bus.get_bus_type_display()} is not allowed on {self}. "
                f"Allowed: {', '.join(allowed)}."
            )

    def __str__(self):
        return f"{self.origin.name} → {self.destination.name}"


class RoutePriceHistory(models.Model):
    """Tracks every price change on a route for audit and analytics."""
    route = models.ForeignKey(Route, on_delete=models.CASCADE, related_name="price_history")
    old_price = models.DecimalField(max_digits=10, decimal_places=2)
    new_price = models.DecimalField(max_digits=10, decimal_places=2)
    old_vip_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    new_vip_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    reason = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.route}: {self.old_price} → {self.new_price} on {self.created_at:%Y-%m-%d}"
