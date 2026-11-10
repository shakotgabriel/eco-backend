"""
Serializers for fraud detection models.
"""
from rest_framework import serializers
from fraud_detection.models import (
    DuplicateBookingAlert,
    SeatScalpingAlert,
    PaymentVelocity,
    FraudAlert,
    NoShowTracking,
    SeatHeatMapData,
    DemandForecast,
)


class DuplicateBookingAlertSerializer(serializers.ModelSerializer):
    first_booking_ref = serializers.CharField(source='first_booking.booking_reference', read_only=True)
    second_booking_ref = serializers.CharField(source='second_booking.booking_reference', read_only=True)
    
    class Meta:
        model = DuplicateBookingAlert
        fields = [
            'id', 'phone_number', 'trip', 'first_booking', 'second_booking',
            'first_booking_ref', 'second_booking_ref', 'time_between_bookings_seconds',
            'status', 'review_notes', 'reviewed_by', 'reviewed_at', 'created_at'
        ]
        read_only_fields = ['created_at', 'reviewed_at']


class SeatScalpingAlertSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)
    
    class Meta:
        model = SeatScalpingAlert
        fields = [
            'id', 'user', 'username', 'pattern', 'total_book_cancel_cycles',
            'affected_seats', 'affected_routes', 'time_window_hours', 'status',
            'reason', 'review_notes', 'reviewed_by', 'reviewed_at', 'created_at'
        ]
        read_only_fields = ['created_at', 'reviewed_at']


class PaymentVelocitySerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentVelocity
        fields = [
            'id', 'user', 'phone_number', 'payment_attempts_24h', 'total_amount_24h',
            'failed_attempts_24h', 'different_payment_methods', 'last_updated',
            'max_attempts_24h', 'max_failed_attempts_24h', 'max_amount_24h'
        ]
        read_only_fields = ['last_updated']


class FraudAlertListSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)
    booking_ref = serializers.CharField(source='booking.booking_reference', read_only=True)
    
    class Meta:
        model = FraudAlert
        fields = [
            'id', 'alert_id', 'alert_type', 'severity', 'status', 'user', 'username',
            'phone_number', 'booking', 'booking_ref', 'description', 'risk_score',
            'detection_rules', 'action_taken', 'created_at'
        ]
        read_only_fields = ['alert_id', 'created_at']


class FraudAlertDetailSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)
    booking_ref = serializers.CharField(source='booking.booking_reference', read_only=True)
    reviewer_username = serializers.CharField(source='reviewed_by.username', read_only=True)
    
    class Meta:
        model = FraudAlert
        fields = [
            'id', 'alert_id', 'alert_type', 'severity', 'status', 'user', 'username',
            'phone_number', 'booking', 'booking_ref', 'description', 'risk_score',
            'detection_rules', 'context_data', 'action_taken', 'action_details',
            'reviewed_by', 'reviewer_username', 'reviewed_at', 'review_notes', 'created_at'
        ]
        read_only_fields = ['alert_id', 'created_at', 'reviewed_at']


class FraudAlertActionSerializer(serializers.Serializer):
    """Handle one-click fraud actions."""
    action = serializers.ChoiceField(choices=['BLOCK_ACCOUNT', 'REFUND', 'RELEASE', 'INVESTIGATE'])
    amount_to_refund = serializers.DecimalField(max_digits=10, decimal_places=2, required=False)
    notes = serializers.CharField(max_length=1000, required=False)


class NoShowTrackingSerializer(serializers.ModelSerializer):
    booking_ref = serializers.CharField(source='booking.booking_reference', read_only=True)
    trip_date = serializers.DateField(source='trip.departure_time.date', read_only=True)
    
    class Meta:
        model = NoShowTracking
        fields = [
            'id', 'booking', 'booking_ref', 'trip', 'route', 'scheduled_departure',
            'departure_time_slot', 'day_of_week', 'passenger_checked_in',
            'passenger_boarded', 'marked_no_show_at', 'marked_no_show_by', 'trip_date'
        ]
        read_only_fields = ['marked_no_show_at']


class SeatHeatMapDataSerializer(serializers.ModelSerializer):
    bus_plate = serializers.CharField(source='bus.plate_number', read_only=True)
    route_display = serializers.CharField(source='route.__str__', read_only=True)
    
    class Meta:
        model = SeatHeatMapData
        fields = [
            'id', 'bus', 'bus_plate', 'route', 'route_display', 'time_period',
            'period_start', 'period_end', 'seat_fill_data', 'total_seats', 'seats_booked',
            'avg_booking_lead_time_hours', 'vip_seats_pct_booked', 'regular_seats_pct_booked',
            'window_seats_pct_booked', 'aisle_seats_pct_booked', 'middle_seats_pct_booked'
        ]
        read_only_fields = [
            'period_start', 'period_end', 'seat_fill_data', 'total_seats', 'seats_booked',
            'avg_booking_lead_time_hours', 'vip_seats_pct_booked', 'regular_seats_pct_booked',
            'window_seats_pct_booked', 'aisle_seats_pct_booked', 'middle_seats_pct_booked'
        ]


class DemandForecastSerializer(serializers.ModelSerializer):
    route_display = serializers.CharField(source='route.__str__', read_only=True)
    
    class Meta:
        model = DemandForecast
        fields = [
            'id', 'route', 'route_display', 'forecast_date', 'historical_bookings_past_14d',
            'avg_booking_lead_time_hours', 'forecast_data', 'confidence_level',
            'actual_results', 'forecast_accuracy'
        ]
        read_only_fields = [
            'forecast_date', 'historical_bookings_past_14d', 'forecast_data',
            'confidence_level', 'actual_results', 'forecast_accuracy'
        ]
