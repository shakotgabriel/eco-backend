"""
Fraud detection and risk management models.
"""
import uuid
from decimal import Decimal
from django.db import models
from django.utils import timezone
from django.conf import settings
from bebeto_backend.base import BaseModel


class DuplicateBookingAlert(BaseModel):
    """Detects same phone + trip booked twice within 5 minutes."""
    STATUS_CHOICES = (
        ("PENDING", "Pending Review"),
        ("FLAGGED", "Flagged for Review"),
        ("RESOLVED", "Resolved"),
        ("FALSE_POSITIVE", "False Positive"),
    )
    
    phone_number = models.CharField(max_length=20, db_index=True)
    trip = models.ForeignKey("trips.Trip", on_delete=models.CASCADE, related_name="duplicate_alerts")
    first_booking = models.ForeignKey(
        "bookings.Booking", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="duplicate_first_alert"
    )
    second_booking = models.ForeignKey(
        "bookings.Booking", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="duplicate_second_alert"
    )
    time_between_bookings_seconds = models.IntegerField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="PENDING")
    review_notes = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["phone_number", "status"], name="idx_dup_phone_status"),
            models.Index(fields=["trip", "status"], name="idx_dup_trip_status"),
        ]
    
    def __str__(self):
        return f"Duplicate Alert: {self.phone_number} on {self.trip}"


class SeatScalpingAlert(BaseModel):
    """Detects accounts booking and cancelling same seats repeatedly (scalping behavior)."""
    STATUS_CHOICES = (
        ("PENDING", "Pending Review"),
        ("FLAGGED", "Flagged for Review"),
        ("RESOLVED", "Resolved"),
        ("ACCOUNT_BLOCKED", "Account Blocked"),
    )
    
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="scalping_alerts")
    pattern = models.JSONField(default=dict, help_text="Seat booking/cancel pattern over time window")
    total_book_cancel_cycles = models.PositiveIntegerField()
    affected_seats = models.JSONField(default=list, help_text="List of affected seat IDs/numbers")
    affected_routes = models.JSONField(default=list, help_text="List of route IDs where scalping occurred")
    time_window_hours = models.PositiveIntegerField(default=24)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="PENDING")
    reason = models.TextField(blank=True)
    review_notes = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="reviewed_scalping_alerts"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "status"], name="idx_scalp_user_status"),
            models.Index(fields=["status", "-created_at"], name="idx_scalp_status_date"),
        ]
    
    def __str__(self):
        return f"Scalping Alert: {self.user.username} ({self.total_book_cancel_cycles} cycles)"


class PaymentVelocity(BaseModel):
    """Tracks payment attempts per user/phone in 24h window for velocity-based fraud detection."""
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="payment_velocity",
        null=True, blank=True
    )
    phone_number = models.CharField(max_length=20, db_index=True)
    payment_attempts_24h = models.JSONField(default=list, help_text="List of payment attempt timestamps (ISO format)")
    total_amount_24h = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    failed_attempts_24h = models.PositiveIntegerField(default=0)
    different_payment_methods = models.JSONField(default=list, help_text="Payment methods used in 24h")
    last_updated = models.DateTimeField(auto_now=True)
    
    # Threshold settings
    max_attempts_24h = models.PositiveIntegerField(default=10)
    max_failed_attempts_24h = models.PositiveIntegerField(default=5)
    max_amount_24h = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal("100000"))
    
    class Meta:
        ordering = ["-last_updated"]
        indexes = [
            models.Index(fields=["phone_number", "last_updated"], name="idx_velocity_phone_updated"),
        ]
    
    def is_velocity_exceeded(self):
        """Check if velocity thresholds exceeded."""
        return (
            len(self.payment_attempts_24h) >= self.max_attempts_24h or
            self.total_amount_24h >= self.max_amount_24h or
            self.failed_attempts_24h >= self.max_failed_attempts_24h
        )
    
    def __str__(self):
        return f"Velocity: {self.phone_number} ({len(self.payment_attempts_24h)} attempts in 24h)"


class FraudAlert(BaseModel):
    """Generic fraud alert queue for manual review."""
    ALERT_TYPE_CHOICES = (
        ("DUPLICATE_BOOKING", "Duplicate Booking"),
        ("VELOCITY_EXCEEDED", "Payment Velocity Exceeded"),
        ("SEAT_SCALPING", "Seat Scalping"),
        ("CHARGEBACK", "Chargeback"),
        ("UNUSUAL_PATTERN", "Unusual Pattern"),
        ("PAYMENT_GATEWAY_DECLINE", "Payment Gateway Decline"),
        ("MANUAL_FLAG", "Manual Flag"),
    )
    
    SEVERITY_CHOICES = (
        ("LOW", "Low"),
        ("MEDIUM", "Medium"),
        ("HIGH", "High"),
        ("CRITICAL", "Critical"),
    )
    
    STATUS_CHOICES = (
        ("PENDING", "Pending"),
        ("UNDER_REVIEW", "Under Review"),
        ("BLOCKED", "Blocked"),
        ("CLEARED", "Cleared"),
    )
    
    alert_id = models.CharField(max_length=50, unique=True, editable=False, db_index=True)
    alert_type = models.CharField(max_length=30, choices=ALERT_TYPE_CHOICES)
    severity = models.CharField(max_length=10, choices=SEVERITY_CHOICES, default="MEDIUM")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="PENDING")
    
    # Link to user/booking/phone
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="fraud_alerts")
    phone_number = models.CharField(max_length=20, db_index=True)
    booking = models.ForeignKey("bookings.Booking", on_delete=models.SET_NULL, null=True, blank=True, related_name="fraud_alerts")
    
    # Alert details
    description = models.TextField()
    risk_score = models.DecimalField(max_digits=3, decimal_places=2, default=0, help_text="0.0 - 1.0 risk score")
    detection_rules = models.JSONField(default=list, help_text="Which rules triggered this alert")
    context_data = models.JSONField(default=dict, help_text="Additional context for investigation")
    
    # Actions
    action_taken = models.CharField(
        max_length=50, blank=True,
        help_text="BLOCK_ACCOUNT, REFUND, RELEASE, INVESTIGATE, etc."
    )
    action_details = models.JSONField(default=dict, help_text="Details of action taken (e.g. refund ID)")
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="reviewed_fraud_alerts"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True)
    
    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "severity"], name="idx_fraud_status_severity"),
            models.Index(fields=["phone_number", "status"], name="idx_fraud_phone_status"),
            models.Index(fields=["user", "status"], name="idx_fraud_user_status"),
            models.Index(fields=["alert_type", "severity"], name="idx_fraud_type_severity"),
        ]
    
    def save(self, *args, **kwargs):
        if not self.alert_id:
            date_str = timezone.now().strftime("%Y%m%d")
            unique_id = uuid.uuid4().hex[:8].upper()
            self.alert_id = f"FRAUD-{date_str}-{unique_id}"
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"{self.alert_id} ({self.alert_type}) - {self.severity}"


class NoShowTracking(BaseModel):
    """Tracks no-shows per route/time slot for analytics."""
    booking = models.OneToOneField("bookings.Booking", on_delete=models.CASCADE, related_name="no_show_tracking")
    trip = models.ForeignKey("trips.Trip", on_delete=models.CASCADE, related_name="no_shows")
    route = models.ForeignKey("routes.Route", on_delete=models.CASCADE, related_name="no_shows")
    
    scheduled_departure = models.DateTimeField()
    departure_time_slot = models.CharField(
        max_length=20, db_index=True,
        help_text="e.g. '06:00-09:00', '12:00-15:00', '18:00-21:00'"
    )
    day_of_week = models.CharField(max_length=3, db_index=True, help_text="MON, TUE, etc.")
    
    # No-show details
    passenger_checked_in = models.BooleanField(default=False)
    passenger_boarded = models.BooleanField(default=False)
    marked_no_show_at = models.DateTimeField(null=True, blank=True)
    marked_no_show_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="marked_no_shows"
    )
    
    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["route", "day_of_week"], name="idx_noshow_route_dow"),
            models.Index(fields=["departure_time_slot", "route"], name="idx_noshow_slot_route"),
            models.Index(fields=["scheduled_departure"], name="idx_noshow_departure"),
        ]
    
    def __str__(self):
        return f"NoShow: {self.booking.booking_reference} on {self.trip}"


class SeatHeatMapData(BaseModel):
    """Analytics: tracks which seats sell first/last by bus layout."""
    bus = models.ForeignKey("buses.Bus", on_delete=models.CASCADE, related_name="seat_heat_maps")
    route = models.ForeignKey("routes.Route", on_delete=models.CASCADE, related_name="seat_heat_maps")
    time_period = models.CharField(
        max_length=20,
        choices=[("HOURLY", "Hourly"), ("DAILY", "Daily"), ("WEEKLY", "Weekly"), ("MONTHLY", "Monthly")],
        default="DAILY"
    )
    
    period_start = models.DateTimeField(db_index=True)
    period_end = models.DateTimeField()
    
    # Seat fill order (seat_number -> order/rank it was booked)
    seat_fill_data = models.JSONField(default=dict, help_text="{seat_number: fill_order, ...}")
    # Aggregate metrics
    total_seats = models.PositiveIntegerField()
    seats_booked = models.PositiveIntegerField()
    avg_booking_lead_time_hours = models.DecimalField(max_digits=5, decimal_places=2)
    vip_seats_pct_booked = models.DecimalField(max_digits=5, decimal_places=2)
    regular_seats_pct_booked = models.DecimalField(max_digits=5, decimal_places=2)
    window_seats_pct_booked = models.DecimalField(max_digits=5, decimal_places=2)
    aisle_seats_pct_booked = models.DecimalField(max_digits=5, decimal_places=2)
    middle_seats_pct_booked = models.DecimalField(max_digits=5, decimal_places=2)
    
    class Meta:
        ordering = ["-period_start"]
        indexes = [
            models.Index(fields=["bus", "time_period"], name="idx_heat_bus_period"),
            models.Index(fields=["route", "period_start"], name="idx_heat_route_start"),
        ]
    
    def __str__(self):
        return f"Heat Map: {self.bus} {self.time_period} ({self.period_start.date()})"


class DemandForecast(BaseModel):
    """Demand forecasting: predicted vs actual bookings 14 days forward."""
    route = models.ForeignKey("routes.Route", on_delete=models.CASCADE, related_name="demand_forecasts")
    forecast_date = models.DateField(db_index=True)
    
    # Historical data for forecasting
    historical_bookings_past_14d = models.JSONField(default=dict, help_text="{date: booking_count}")
    avg_booking_lead_time_hours = models.DecimalField(max_digits=5, decimal_places=2)
    
    # Forecast (14 days forward from forecast_date)
    forecast_data = models.JSONField(default=dict, help_text="{date: predicted_bookings}")
    confidence_level = models.DecimalField(max_digits=3, decimal_places=2, help_text="0.0 - 1.0 confidence")
    
    # Actual results (as days pass)
    actual_results = models.JSONField(default=dict, help_text="{date: actual_bookings}")
    forecast_accuracy = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, help_text="MAE or RMSE")
    
    class Meta:
        ordering = ["-forecast_date"]
        indexes = [
            models.Index(fields=["route", "forecast_date"], name="idx_forecast_route_date"),
        ]
    
    def __str__(self):
        return f"Forecast: {self.route.origin} → {self.route.destination} ({self.forecast_date})"
