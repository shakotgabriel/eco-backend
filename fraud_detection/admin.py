"""
Django admin for fraud detection models.
"""
from django.contrib import admin
from django.utils.html import format_html
from fraud_detection.models import (
    DuplicateBookingAlert,
    SeatScalpingAlert,
    PaymentVelocity,
    FraudAlert,
    NoShowTracking,
    SeatHeatMapData,
    DemandForecast,
)


@admin.register(DuplicateBookingAlert)
class DuplicateBookingAlertAdmin(admin.ModelAdmin):
    list_display = ['phone_number', 'trip', 'time_between_bookings_seconds', 'status_badge', 'created_at']
    list_filter = ['status', 'created_at']
    search_fields = ['phone_number', 'trip__id']
    readonly_fields = ['created_at', 'first_booking', 'second_booking']
    
    fieldsets = (
        ('Alert Info', {'fields': ('phone_number', 'trip', 'time_between_bookings_seconds')}),
        ('Bookings', {'fields': ('first_booking', 'second_booking')}),
        ('Status', {'fields': ('status', 'review_notes', 'reviewed_by', 'reviewed_at')}),
        ('Metadata', {'fields': ('created_at',)}),
    )
    
    def status_badge(self, obj):
        colors = {'PENDING': '#FFA500', 'FLAGGED': '#FF0000', 'RESOLVED': '#00AA00', 'FALSE_POSITIVE': '#0000FF'}
        return format_html(
            '<span style="background-color: {}; color: white; padding: 5px 10px; border-radius: 3px;">{}</span>',
            colors.get(obj.status, '#808080'),
            obj.status
        )
    status_badge.short_description = 'Status'


@admin.register(SeatScalpingAlert)
class SeatScalpingAlertAdmin(admin.ModelAdmin):
    list_display = ['user', 'total_book_cancel_cycles', 'status_badge', 'created_at']
    list_filter = ['status', 'created_at']
    search_fields = ['user__username', 'user__email']
    readonly_fields = ['created_at', 'pattern']
    
    fieldsets = (
        ('User', {'fields': ('user',)}),
        ('Pattern', {'fields': ('pattern', 'total_book_cancel_cycles', 'affected_seats', 'affected_routes')}),
        ('Details', {'fields': ('time_window_hours', 'reason')}),
        ('Review', {'fields': ('status', 'review_notes', 'reviewed_by', 'reviewed_at')}),
        ('Metadata', {'fields': ('created_at',)}),
    )
    
    def status_badge(self, obj):
        colors = {'PENDING': '#FFA500', 'FLAGGED': '#FF0000', 'RESOLVED': '#00AA00', 'ACCOUNT_BLOCKED': '#880000'}
        return format_html(
            '<span style="background-color: {}; color: white; padding: 5px 10px; border-radius: 3px;">{}</span>',
            colors.get(obj.status, '#808080'),
            obj.status
        )
    status_badge.short_description = 'Status'


@admin.register(FraudAlert)
class FraudAlertAdmin(admin.ModelAdmin):
    list_display = ['alert_id', 'alert_type', 'severity_badge', 'status_badge', 'risk_score_display', 'created_at']
    list_filter = ['severity', 'status', 'alert_type', 'created_at']
    search_fields = ['alert_id', 'phone_number', 'user__username']
    readonly_fields = ['alert_id', 'created_at']
    
    fieldsets = (
        ('Alert', {'fields': ('alert_id', 'alert_type', 'severity', 'risk_score')}),
        ('Target', {'fields': ('user', 'phone_number', 'booking')}),
        ('Details', {'fields': ('description', 'detection_rules', 'context_data')}),
        ('Action', {'fields': ('status', 'action_taken', 'action_details')}),
        ('Review', {'fields': ('reviewed_by', 'reviewed_at', 'review_notes')}),
        ('Metadata', {'fields': ('created_at',)}),
    )
    
    def severity_badge(self, obj):
        colors = {'LOW': '#00AA00', 'MEDIUM': '#FFA500', 'HIGH': '#FF6600', 'CRITICAL': '#FF0000'}
        return format_html(
            '<span style="background-color: {}; color: white; padding: 5px 10px; border-radius: 3px;">{}</span>',
            colors.get(obj.severity, '#808080'),
            obj.severity
        )
    severity_badge.short_description = 'Severity'
    
    def status_badge(self, obj):
        colors = {'PENDING': '#FFA500', 'UNDER_REVIEW': '#FF6600', 'BLOCKED': '#FF0000', 'CLEARED': '#00AA00'}
        return format_html(
            '<span style="background-color: {}; color: white; padding: 5px 10px; border-radius: 3px;">{}</span>',
            colors.get(obj.status, '#808080'),
            obj.status
        )
    status_badge.short_description = 'Status'
    
    def risk_score_display(self, obj):
        return f"{float(obj.risk_score):.2f}"
    risk_score_display.short_description = 'Risk'


@admin.register(PaymentVelocity)
class PaymentVelocityAdmin(admin.ModelAdmin):
    list_display = ['phone_number', 'total_amount_24h', 'payment_attempts_24h_count', 'is_velocity_exceeded_display', 'last_updated']
    list_filter = ['last_updated']
    search_fields = ['phone_number', 'user__username']
    readonly_fields = ['last_updated', 'payment_attempts_24h', 'different_payment_methods']
    
    fieldsets = (
        ('Identity', {'fields': ('user', 'phone_number')}),
        ('Activity (24h)', {'fields': ('payment_attempts_24h', 'total_amount_24h', 'failed_attempts_24h', 'different_payment_methods')}),
        ('Thresholds', {'fields': ('max_attempts_24h', 'max_failed_attempts_24h', 'max_amount_24h')}),
        ('Metadata', {'fields': ('last_updated',)}),
    )
    
    def payment_attempts_24h_count(self, obj):
        return len(obj.payment_attempts_24h)
    payment_attempts_24h_count.short_description = 'Attempts'
    
    def is_velocity_exceeded_display(self, obj):
        exceeded = obj.is_velocity_exceeded()
        color = '#FF0000' if exceeded else '#00AA00'
        text = 'EXCEEDED' if exceeded else 'OK'
        return format_html(
            '<span style="background-color: {}; color: white; padding: 5px 10px; border-radius: 3px;">{}</span>',
            color, text
        )
    is_velocity_exceeded_display.short_description = 'Velocity Status'


@admin.register(NoShowTracking)
class NoShowTrackingAdmin(admin.ModelAdmin):
    list_display = ['booking', 'route', 'departure_time_slot', 'day_of_week', 'passenger_boarded', 'marked_no_show_at']
    list_filter = ['departure_time_slot', 'day_of_week', 'passenger_boarded', 'marked_no_show_at']
    search_fields = ['booking__booking_reference', 'route__id']
    readonly_fields = ['created_at']
    
    fieldsets = (
        ('Booking', {'fields': ('booking', 'trip', 'route')}),
        ('Schedule', {'fields': ('scheduled_departure', 'departure_time_slot', 'day_of_week')}),
        ('Status', {'fields': ('passenger_checked_in', 'passenger_boarded', 'marked_no_show_at', 'marked_no_show_by')}),
        ('Metadata', {'fields': ('created_at',)}),
    )


@admin.register(SeatHeatMapData)
class SeatHeatMapDataAdmin(admin.ModelAdmin):
    list_display = ['bus', 'route', 'time_period', 'period_start', 'seats_booked', 'vip_seats_pct_booked']
    list_filter = ['time_period', 'period_start']
    search_fields = ['bus__plate_number', 'route__id']
    readonly_fields = ['seat_fill_data', 'created_at']
    
    fieldsets = (
        ('Bus & Route', {'fields': ('bus', 'route')}),
        ('Period', {'fields': ('time_period', 'period_start', 'period_end')}),
        ('Metrics', {'fields': ('total_seats', 'seats_booked', 'avg_booking_lead_time_hours')}),
        ('Seat Type %', {'fields': ('vip_seats_pct_booked', 'regular_seats_pct_booked')}),
        ('Seat Position %', {'fields': ('window_seats_pct_booked', 'aisle_seats_pct_booked', 'middle_seats_pct_booked')}),
        ('Data', {'fields': ('seat_fill_data',)}),
        ('Metadata', {'fields': ('created_at',)}),
    )


@admin.register(DemandForecast)
class DemandForecastAdmin(admin.ModelAdmin):
    list_display = ['route', 'forecast_date', 'confidence_level', 'forecast_accuracy', 'created_at']
    list_filter = ['forecast_date', 'created_at']
    search_fields = ['route__id']
    readonly_fields = ['forecast_data', 'actual_results', 'created_at']
    
    fieldsets = (
        ('Route', {'fields': ('route',)}),
        ('Forecast', {'fields': ('forecast_date', 'forecast_data', 'confidence_level')}),
        ('Historical', {'fields': ('historical_bookings_past_14d', 'avg_booking_lead_time_hours')}),
        ('Actual', {'fields': ('actual_results', 'forecast_accuracy')}),
        ('Metadata', {'fields': ('created_at',)}),
    )
