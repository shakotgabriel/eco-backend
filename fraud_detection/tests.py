"""
Unit tests for fraud detection module.
Run with: python manage.py test fraud_detection
"""
from django.test import TestCase, Client
from django.utils import timezone
from django.contrib.auth import get_user_model
from decimal import Decimal
from datetime import timedelta

from bookings.models import Booking
from payments.models import Payment
from trips.models import Trip
from routes.models import Route
from cities.models import City
from buses.models import Bus
from fraud_detection.models import (
    DuplicateBookingAlert, SeatScalpingAlert, PaymentVelocity,
    FraudAlert, NoShowTracking, SeatHeatMapData, DemandForecast
)
from fraud_detection.services import (
    FraudDetectionService, NoShowService, SeatHeatMapService, DemandForecastService
)

User = get_user_model()


class DuplicateBookingDetectionTest(TestCase):
    """Test duplicate booking detection."""
    
    def setUp(self):
        # Create test cities and route
        self.origin = City.objects.create(name="Kampala", code="KLA")
        self.dest = City.objects.create(name="Jinja", code="JNJ")
        self.route = Route.objects.create(
            origin=self.origin,
            destination=self.dest,
            base_price=Decimal("50000"),
            distance_km=100
        )
        
        # Create test bus
        self.bus = Bus.objects.create(
            plate_number="UB-100-ABC",
            capacity=40,
            bus_type="COMFORT_CRUISER"
        )
        
        # Create test trip
        self.trip = Trip.objects.create(
            route=self.route,
            bus=self.bus,
            departure_time=timezone.now() + timedelta(hours=2),
            arrival_time=timezone.now() + timedelta(hours=4),
            regular_price=Decimal("50000"),
            vip_price=Decimal("75000")
        )
    
    def test_detect_duplicate_booking_same_phone_trip_5min(self):
        """Test duplicate detection triggers for same phone + trip in 5 min."""
        phone = "+256701234567"
        
        # Create first booking
        booking1 = Booking.objects.create(
            trip=self.trip,
            payer_name="John Doe",
            payment_phone=phone,
            number_of_seats=2,
            total_price=Decimal("100000"),
            status="PENDING"
        )
        
        # Create second booking (same phone, trip, within 5 min)
        booking2 = Booking.objects.create(
            trip=self.trip,
            payer_name="John Doe",
            payment_phone=phone,
            number_of_seats=1,
            total_price=Decimal("50000"),
            status="PENDING"
        )
        
        # Detect duplicate
        alert = FraudDetectionService.detect_duplicate_booking(phone, self.trip.id)
        
        self.assertIsNotNone(alert)
        self.assertEqual(alert.phone_number, phone)
        self.assertEqual(alert.trip, self.trip)
        self.assertEqual(alert.first_booking, booking1)
        self.assertEqual(alert.second_booking, booking2)
        self.assertEqual(alert.status, "PENDING")


class PaymentVelocityTest(TestCase):
    """Test payment velocity detection."""
    
    def test_payment_velocity_exceeds_threshold(self):
        """Test velocity threshold detection."""
        phone = "+256701234567"
        
        velocity = PaymentVelocity.objects.create(
            phone_number=phone,
            max_attempts_24h=10,
            max_failed_attempts_24h=5
        )
        
        # Add 11 attempts
        for i in range(11):
            velocity.payment_attempts_24h.append(timezone.now().isoformat())
        velocity.save()
        
        # Check velocity exceeded
        self.assertTrue(velocity.is_velocity_exceeded())
    
    def test_payment_velocity_below_threshold(self):
        """Test velocity OK below threshold."""
        phone = "+256701234567"
        
        velocity = PaymentVelocity.objects.create(
            phone_number=phone,
            max_attempts_24h=10,
            max_failed_attempts_24h=5
        )
        
        # Add 5 attempts
        for i in range(5):
            velocity.payment_attempts_24h.append(timezone.now().isoformat())
        velocity.save()
        
        # Check velocity OK
        self.assertFalse(velocity.is_velocity_exceeded())


class FraudAlertTest(TestCase):
    """Test fraud alert creation and actions."""
    
    def setUp(self):
        self.admin_user = User.objects.create_user(
            username='admin',
            password='pass',
            role='ADMIN'
        )
        self.staff_user = User.objects.create_user(
            username='staff',
            password='pass',
            role='STAFF'
        )
    
    def test_create_fraud_alert(self):
        """Test fraud alert creation."""
        alert = FraudDetectionService.create_fraud_alert(
            alert_type='VELOCITY_EXCEEDED',
            phone_number='+256701234567',
            severity='HIGH',
            description='Payment velocity exceeded',
            risk_score=Decimal('0.8')
        )
        
        self.assertEqual(alert.alert_type, 'VELOCITY_EXCEEDED')
        self.assertEqual(alert.severity, 'HIGH')
        self.assertEqual(alert.status, 'PENDING')
        self.assertTrue(alert.alert_id.startswith('FRAUD-'))
    
    def test_fraud_alert_risk_score(self):
        """Test risk score assignment."""
        alert = FraudAlert.objects.create(
            alert_type='DUPLICATE_BOOKING',
            severity='MEDIUM',
            phone_number='+256701234567',
            description='Test',
            risk_score=Decimal('0.65')
        )
        
        self.assertEqual(float(alert.risk_score), 0.65)
        self.assertGreaterEqual(alert.risk_score, 0)
        self.assertLessEqual(alert.risk_score, 1)


class NoShowTrackingTest(TestCase):
    """Test no-show tracking."""
    
    def setUp(self):
        self.origin = City.objects.create(name="Kampala", code="KLA")
        self.dest = City.objects.create(name="Jinja", code="JNJ")
        self.route = Route.objects.create(
            origin=self.origin,
            destination=self.dest,
            base_price=Decimal("50000")
        )
        self.bus = Bus.objects.create(plate_number="UB-100-ABC", capacity=40)
        self.trip = Trip.objects.create(
            route=self.route,
            bus=self.bus,
            departure_time=timezone.now() + timedelta(hours=2),
            arrival_time=timezone.now() + timedelta(hours=4),
            regular_price=Decimal("50000")
        )
        self.booking = Booking.objects.create(
            trip=self.trip,
            payer_name="John Doe",
            payment_phone="+256701234567",
            number_of_seats=1,
            total_price=Decimal("50000"),
            status="CONFIRMED"
        )
    
    def test_mark_no_show(self):
        """Test marking booking as no-show."""
        no_show = NoShowService.mark_no_show(self.booking.id)
        
        self.assertEqual(no_show.booking, self.booking)
        self.assertEqual(no_show.trip, self.trip)
        self.assertEqual(no_show.route, self.route)
        self.assertIsNotNone(no_show.marked_no_show_at)
    
    def test_no_show_rate_calculation(self):
        """Test no-show rate calculation."""
        # Create 10 bookings, mark 2 as no-show
        for i in range(10):
            booking = Booking.objects.create(
                trip=self.trip,
                payer_name=f"User {i}",
                payment_phone=f"+25670123456{i}",
                number_of_seats=1,
                total_price=Decimal("50000"),
                status="CONFIRMED"
            )
            if i < 2:
                NoShowService.mark_no_show(booking.id)
        
        rate_data = NoShowService.get_no_show_rate(route_id=self.route.id, days=30)
        
        self.assertEqual(rate_data['no_show_count'], 2)


class SeatHeatMapTest(TestCase):
    """Test seat heat map generation."""
    
    def setUp(self):
        self.origin = City.objects.create(name="Kampala", code="KLA")
        self.dest = City.objects.create(name="Jinja", code="JNJ")
        self.route = Route.objects.create(
            origin=self.origin,
            destination=self.dest,
            base_price=Decimal("50000")
        )
        self.bus = Bus.objects.create(plate_number="UB-100-ABC", capacity=40)
    
    def test_generate_heat_map(self):
        """Test heat map generation."""
        trip = Trip.objects.create(
            route=self.route,
            bus=self.bus,
            departure_time=timezone.now() + timedelta(hours=2),
            arrival_time=timezone.now() + timedelta(hours=4),
            regular_price=Decimal("50000")
        )
        
        heat_map = SeatHeatMapService.generate_seat_heat_map(
            bus_id=self.bus.id,
            route_id=self.route.id,
            period_start=timezone.now() - timedelta(days=1),
            period_end=timezone.now()
        )
        
        self.assertEqual(heat_map.bus, self.bus)
        self.assertEqual(heat_map.route, self.route)
        self.assertGreater(heat_map.total_seats, 0)


class DemandForecastTest(TestCase):
    """Test demand forecasting."""
    
    def setUp(self):
        self.origin = City.objects.create(name="Kampala", code="KLA")
        self.dest = City.objects.create(name="Jinja", code="JNJ")
        self.route = Route.objects.create(
            origin=self.origin,
            destination=self.dest,
            base_price=Decimal("50000")
        )
    
    def test_generate_forecast(self):
        """Test forecast generation."""
        forecast = DemandForecastService.generate_forecast(self.route.id)
        
        self.assertEqual(forecast.route, self.route)
        self.assertEqual(forecast.forecast_date, timezone.now().date())
        self.assertGreater(len(forecast.forecast_data), 0)
        self.assertGreater(forecast.confidence_level, 0)
