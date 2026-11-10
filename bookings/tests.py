from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from bebeto_backend.testing import make_booking, make_trip
from payments.models import Payment, Refund


class CancelBookingTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.trip = make_trip()
        self.booking = make_booking(self.trip)
        self.payment = Payment.objects.create(
            booking=self.booking,
            amount=Decimal("10000.00"),
            currency="KES",
            method="MPESA",
            status="COMPLETED",
        )

    def _cancel(self, booking, **extra):
        body = {
            "booking_reference": booking.booking_reference,
            "payment_phone": str(booking.payment_phone),
            **extra,
        }
        return self.client.post(f"/api/v1/bookings/{booking.pk}/cancel/", body, format="json")

    def test_cancel_releases_seats_without_refund(self):
        seats_before = self.trip.available_seats

        response = self._cancel(self.booking, reason="Change of plans")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["refund_amount"], "0.00")
        self.assertEqual(response.data["refund_percentage"], 0)
        self.booking.refresh_from_db()
        self.payment.refresh_from_db()
        self.trip.refresh_from_db()
        self.assertEqual(self.booking.status, "CANCELLED")
        self.assertEqual(self.payment.status, "COMPLETED")
        self.assertFalse(Refund.objects.exists())
        self.assertEqual(self.trip.available_seats, seats_before + self.booking.number_of_seats)

    def test_cancel_requires_booking_credentials(self):
        response = self.client.post(
            f"/api/v1/bookings/{self.booking.pk}/cancel/",
            {"booking_reference": self.booking.booking_reference, "payment_phone": "+254700000000"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_passenger_cannot_cancel_after_departure(self):
        departed = make_booking(make_trip(departs_in=-timedelta(hours=1), plate="KBZ200B"))
        response = self._cancel(departed)
        self.assertEqual(response.status_code, 400)

    def test_manager_can_cancel_after_departure(self):
        manager = get_user_model().objects.create_user(username="mgr", password="pass1234", role="MANAGER")
        departed = make_booking(make_trip(departs_in=-timedelta(hours=1), plate="KBZ300C"))
        self.client.force_authenticate(manager)
        response = self.client.post(f"/api/v1/bookings/{departed.pk}/cancel/", {}, format="json")
        self.assertEqual(response.status_code, 200)

    def test_reschedule_endpoint_is_gone(self):
        response = self.client.post(f"/api/v1/bookings/{self.booking.pk}/reschedule/", {}, format="json")
        self.assertEqual(response.status_code, 404)
