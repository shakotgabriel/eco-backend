"""
Fraud detection service logic and algorithms.
"""
import json
from datetime import timedelta
from decimal import Decimal
from django.utils import timezone
from django.db.models import Q, Count, Sum, Avg
from fraud_detection.models import (
    DuplicateBookingAlert,
    SeatScalpingAlert,
    PaymentVelocity,
    FraudAlert,
    NoShowTracking,
    SeatHeatMapData,
    DemandForecast,
)
from bookings.models import Booking
from payments.models import Payment


class FraudDetectionService:
    """Core fraud detection logic."""
    
    RISK_SCORE_WEIGHTS = {
        'duplicate_booking': 0.7,
        'velocity_exceeded': 0.8,
        'seat_scalping': 0.9,
        'payment_decline': 0.6,
    }
    
    @staticmethod
    def detect_duplicate_booking(phone_number: str, trip_id: int) -> DuplicateBookingAlert:
        """
        Detect if same phone + trip booked twice within 5 minutes.
        Returns alert if detected, None if not suspicious.
        """
        five_min_ago = timezone.now() - timedelta(minutes=5)
        
        bookings = Booking.objects.filter(
            payment_phone=phone_number,
            trip_id=trip_id,
            created_at__gte=five_min_ago,
            status__in=['PENDING', 'CONFIRMED']
        ).order_by('created_at')
        
        if bookings.count() >= 2:
            first = bookings.first()
            second = bookings[1]
            time_diff = (second.created_at - first.created_at).total_seconds()
            
            alert = DuplicateBookingAlert.objects.create(
                phone_number=phone_number,
                trip_id=trip_id,
                first_booking=first,
                second_booking=second,
                time_between_bookings_seconds=int(time_diff),
                status='PENDING'
            )
            return alert
        return None
    
    @staticmethod
    def detect_seat_scalping(user_id: int, time_window_hours: int = 24) -> SeatScalpingAlert:
        """
        Detect users booking and cancelling same seats repeatedly (scalping).
        Thresholds: 5+ book-cancel cycles in time window triggers alert.
        """
        cutoff_time = timezone.now() - timedelta(hours=time_window_hours)
        
        # Get bookings for user in time window
        bookings = Booking.objects.filter(
            user_id=user_id,
            created_at__gte=cutoff_time
        ).values('trip', 'status').annotate(
            count=Count('id'),
            cancellations=Count('id', filter=Q(status='CANCELLED')),
            confirmations=Count('id', filter=Q(status__in=['CONFIRMED', 'COMPLETED']))
        )
        
        # Calculate scalping pattern
        total_book_cancel = 0
        seat_patterns = {}
        route_ids = set()
        
        for booking_stat in bookings:
            if booking_stat['cancellations'] > 0 and booking_stat['confirmations'] > 0:
                total_book_cancel += booking_stat['cancellations']
                route_ids.add(booking_stat['trip'])
        
        # If 5+ book-cancel cycles, flag it
        if total_book_cancel >= 5:
            alert = SeatScalpingAlert.objects.create(
                user_id=user_id,
                pattern={'book_cancel_cycles': total_book_cancel},
                total_book_cancel_cycles=total_book_cancel,
                affected_seats=[],  # Would need seat-level tracking for detail
                affected_routes=list(route_ids),
                time_window_hours=time_window_hours,
                status='PENDING',
                reason=f'Detected {total_book_cancel} book-cancel cycles in {time_window_hours}h window'
            )
            return alert
        return None
    
    @staticmethod
    def check_payment_velocity(phone_number: str) -> bool:
        """
        Check if phone number exceeded payment velocity thresholds (24h window).
        Returns True if threshold exceeded.
        """
        velocity, _ = PaymentVelocity.objects.get_or_create(phone_number=phone_number)
        
        # Clean up old attempts (>24h old)
        cutoff = timezone.now() - timedelta(hours=24)
        updated_attempts = [
            ts for ts in velocity.payment_attempts_24h
            if timezone.datetime.fromisoformat(ts) > cutoff
        ]
        velocity.payment_attempts_24h = updated_attempts
        velocity.save()
        
        return velocity.is_velocity_exceeded()
    
    @staticmethod
    def create_fraud_alert(
        alert_type: str,
        phone_number: str,
        severity: str = 'MEDIUM',
        user=None,
        booking=None,
        description: str = '',
        detection_rules: list = None,
        context_data: dict = None,
        risk_score: Decimal = Decimal('0.5')
    ) -> FraudAlert:
        """Create a fraud alert and queue for review."""
        alert = FraudAlert.objects.create(
            alert_type=alert_type,
            severity=severity,
            status='PENDING',
            user=user,
            phone_number=phone_number,
            booking=booking,
            description=description,
            detection_rules=detection_rules or [],
            context_data=context_data or {},
            risk_score=risk_score
        )
        
        # Send to Slack/monitoring if critical
        if severity in ['HIGH', 'CRITICAL']:
            from fraud_detection.tasks import alert_fraud_team
            alert_fraud_team.delay(alert.alert_id)
        
        return alert
    
    @staticmethod
    def block_account_and_refund(fraud_alert: FraudAlert, amount: Decimal = None) -> dict:
        """
        One-click action: block user account and refund their payment.
        """
        results = {
            'account_blocked': False,
            'refunds_issued': 0,
            'refund_amount': Decimal('0'),
            'errors': []
        }
        
        if not fraud_alert.user:
            results['errors'].append('No user associated with alert')
            return results
        
        # Block account
        user = fraud_alert.user
        user.is_active = False
        user.save()
        results['account_blocked'] = True
        
        # Find and refund payments
        if fraud_alert.booking:
            bookings_to_refund = [fraud_alert.booking]
        else:
            # Refund all recent bookings (last 24h)
            bookings_to_refund = Booking.objects.filter(
                user=user,
                created_at__gte=timezone.now() - timedelta(hours=24)
            )
        
        for booking in bookings_to_refund:
            payments = booking.payments.filter(status__in=['COMPLETED', 'PENDING'])
            for payment in payments:
                refund_amount = amount or payment.amount
                payment.status = 'REFUNDED'
                payment.save()
                results['refunds_issued'] += 1
                results['refund_amount'] += refund_amount
        
        # Update fraud alert
        fraud_alert.action_taken = 'BLOCK_ACCOUNT'
        fraud_alert.action_details = {
            'refunds_issued': results['refunds_issued'],
            'total_refund': str(results['refund_amount']),
            'timestamp': timezone.now().isoformat()
        }
        fraud_alert.status = 'BLOCKED'
        fraud_alert.reviewed_by = None  # Would be set by admin
        fraud_alert.reviewed_at = timezone.now()
        fraud_alert.save()
        
        return results


class NoShowService:
    """Track and analyze no-show patterns."""
    
    @staticmethod
    def mark_no_show(booking_id: int, marked_by=None) -> NoShowTracking:
        """Mark a booking as no-show and track it."""
        booking = Booking.objects.get(id=booking_id)
        trip = booking.trip
        
        time_slot = NoShowService._get_time_slot(trip.departure_time)
        day_of_week = trip.departure_time.strftime('%a').upper()
        
        no_show = NoShowTracking.objects.get_or_create(
            booking=booking,
            defaults={
                'trip': trip,
                'route': trip.route,
                'scheduled_departure': trip.departure_time,
                'departure_time_slot': time_slot,
                'day_of_week': day_of_week,
                'passenger_boarded': False,
            }
        )[0]
        
        no_show.marked_no_show_at = timezone.now()
        no_show.marked_no_show_by = marked_by
        no_show.save()
        
        return no_show
    
    @staticmethod
    def get_no_show_rate(route_id: int = None, time_slot: str = None, days: int = 30) -> dict:
        """Calculate no-show rate by route and time slot."""
        cutoff = timezone.now() - timedelta(days=days)
        
        query = NoShowTracking.objects.filter(scheduled_departure__gte=cutoff)
        
        if route_id:
            query = query.filter(route_id=route_id)
        if time_slot:
            query = query.filter(departure_time_slot=time_slot)
        
        total_bookings = Booking.objects.filter(
            created_at__gte=cutoff,
            status__in=['CONFIRMED', 'COMPLETED']
        )
        if route_id:
            total_bookings = total_bookings.filter(trip__route_id=route_id)
        
        no_show_count = query.count()
        total_count = total_bookings.count()
        
        rate = (no_show_count / total_count * 100) if total_count > 0 else 0
        
        return {
            'no_show_count': no_show_count,
            'total_bookings': total_count,
            'no_show_rate_pct': round(rate, 2),
            'time_period_days': days
        }
    
    @staticmethod
    def _get_time_slot(departure_time) -> str:
        """Categorize departure time into time slots."""
        hour = departure_time.hour
        if 6 <= hour < 9:
            return '06:00-09:00'
        elif 9 <= hour < 12:
            return '09:00-12:00'
        elif 12 <= hour < 15:
            return '12:00-15:00'
        elif 15 <= hour < 18:
            return '15:00-18:00'
        elif 18 <= hour < 21:
            return '18:00-21:00'
        else:
            return '21:00-06:00'


class SeatHeatMapService:
    """Calculate seat heat maps for demand analytics."""
    
    @staticmethod
    def generate_seat_heat_map(bus_id: int, route_id: int, period_start, period_end):
        """Generate seat heat map for a bus/route over time period."""
        from bookings.models import Booking
        from seats.models import Seat
        
        bookings = Booking.objects.filter(
            trip__bus_id=bus_id,
            trip__route_id=route_id,
            created_at__gte=period_start,
            created_at__lte=period_end,
            status__in=['CONFIRMED', 'COMPLETED']
        ).select_related('trip')
        
        seat_fill_order = {}
        seat_types = {'vip': 0, 'regular': 0}
        seat_positions = {'window': 0, 'aisle': 0, 'middle': 0}
        
        # Simulate booking order to track fill pattern
        for i, booking in enumerate(bookings.order_by('created_at')):
            # In real scenario, would track actual seat assignments
            seat_fill_order[f'seat_{i}'] = i
        
        # Get seat statistics
        seats = Seat.objects.filter(bus_id=bus_id)
        total_seats = seats.count()
        booked_seats = len(seat_fill_order)
        
        vip_seats = seats.filter(seat_type='VIP').count()
        vip_booked = int(booked_seats * (vip_seats / total_seats)) if total_seats > 0 else 0
        
        window_seats = seats.filter(position='WINDOW').count()
        window_booked = int(booked_seats * (window_seats / total_seats)) if total_seats > 0 else 0
        
        avg_lead_time = SeatHeatMapService._calculate_avg_lead_time(bookings)
        
        heat_map = SeatHeatMapData.objects.create(
            bus_id=bus_id,
            route_id=route_id,
            time_period='DAILY',
            period_start=period_start,
            period_end=period_end,
            seat_fill_data=seat_fill_order,
            total_seats=total_seats,
            seats_booked=booked_seats,
            avg_booking_lead_time_hours=avg_lead_time,
            vip_seats_pct_booked=Decimal(vip_booked / vip_seats * 100) if vip_seats > 0 else Decimal(0),
            regular_seats_pct_booked=Decimal(100 - (vip_booked / vip_seats * 100)) if vip_seats > 0 else Decimal(0),
            window_seats_pct_booked=Decimal(window_booked / window_seats * 100) if window_seats > 0 else Decimal(0),
            aisle_seats_pct_booked=Decimal(50),  # Simplified
            middle_seats_pct_booked=Decimal(50),  # Simplified
        )
        
        return heat_map
    
    @staticmethod
    def _calculate_avg_lead_time(bookings) -> Decimal:
        """Calculate average booking lead time in hours."""
        lead_times = []
        for booking in bookings:
            if booking.trip and booking.trip.departure_time:
                lead_time = (booking.trip.departure_time - booking.created_at).total_seconds() / 3600
                lead_times.append(lead_time)
        
        if lead_times:
            avg = sum(lead_times) / len(lead_times)
            return Decimal(str(round(avg, 2)))
        return Decimal('0')


class DemandForecastService:
    """Simple demand forecasting based on historical data."""
    
    @staticmethod
    def generate_forecast(route_id: int):
        """
        Generate 14-day demand forecast for a route.
        Uses simple MA (moving average) + seasonal adjustment.
        """
        from trips.models import Trip
        
        # Get last 30 days of booking data
        cutoff = timezone.now() - timedelta(days=30)
        bookings_by_date = {}
        
        bookings = Booking.objects.filter(
            trip__route_id=route_id,
            created_at__gte=cutoff,
            status__in=['CONFIRMED', 'COMPLETED']
        ).extra(
            select={'booking_date': 'DATE(created_at)'}
        ).values('booking_date').annotate(count=Count('id'))
        
        for entry in bookings:
            bookings_by_date[entry['booking_date'].isoformat()] = entry['count']
        
        # Calculate average and forecast 14 days
        avg_daily = sum(bookings_by_date.values()) / len(bookings_by_date) if bookings_by_date else 0
        forecast_data = {}
        
        for i in range(14):
            forecast_date = (timezone.now() + timedelta(days=i)).date()
            # Simple forecast: avg with slight random variation
            predicted = int(avg_daily * (0.9 + (i % 3) * 0.1))
            forecast_data[forecast_date.isoformat()] = predicted
        
        # Calculate confidence (lower if data sparse)
        confidence = Decimal(str(min(0.95, 0.5 + len(bookings_by_date) / 100)))
        
        forecast = DemandForecast.objects.create(
            route_id=route_id,
            forecast_date=timezone.now().date(),
            historical_bookings_past_14d=bookings_by_date,
            avg_booking_lead_time_hours=Decimal('48'),  # Simplified
            forecast_data=forecast_data,
            confidence_level=confidence,
            actual_results={}
        )
        
        return forecast
    
    @staticmethod
    def update_forecast_with_actuals(forecast: DemandForecast):
        """Update forecast with actual booking data as days pass."""
        from bookings.models import Booking
        
        actual_results = {}
        for forecast_date_str in forecast.forecast_data.keys():
            forecast_date = timezone.datetime.fromisoformat(forecast_date_str).date()
            
            if forecast_date <= timezone.now().date():
                actual_bookings = Booking.objects.filter(
                    trip__route=forecast.route,
                    created_at__date=forecast_date,
                    status__in=['CONFIRMED', 'COMPLETED']
                ).count()
                actual_results[forecast_date_str] = actual_bookings
        
        forecast.actual_results = actual_results
        
        # Calculate forecast accuracy (MAE)
        if actual_results:
            errors = []
            for date_str, actual in actual_results.items():
                predicted = forecast.forecast_data.get(date_str, 0)
                errors.append(abs(predicted - actual))
            
            mae = sum(errors) / len(errors)
            forecast.forecast_accuracy = Decimal(str(mae))
        
        forecast.save()
        return forecast
