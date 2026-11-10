from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from bebeto_backend.testing import make_booking, make_trip
from tickets.models import Ticket
from tickets.signing import ticket_download_token


class PublicTicketAccessTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.booking = make_booking(make_trip())
        self.ticket = Ticket.objects.create(
            booking=self.booking,
            first_name="Grace",
            last_name="Akol",
            phone_number="+254712345678",
            id_number="A123",
            price=Decimal("5000.00"),
        )

    def test_pdf_requires_token(self):
        response = self.client.get(f"/api/v1/tickets/pdf/{self.ticket.ticket_number}/")
        self.assertEqual(response.status_code, 403)

    def test_pdf_rejects_wrong_token(self):
        response = self.client.get(f"/api/v1/tickets/pdf/{self.ticket.ticket_number}/?token=nope")
        self.assertEqual(response.status_code, 403)

    def test_pdf_with_token(self):
        token = ticket_download_token(self.ticket.ticket_number)
        response = self.client.get(f"/api/v1/tickets/pdf/{self.ticket.ticket_number}/?token={token}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")

    def test_qr_requires_token(self):
        response = self.client.get(f"/api/v1/tickets/qr/{self.ticket.ticket_number}/")
        self.assertEqual(response.status_code, 403)

    def test_staff_can_fetch_pdf_without_token(self):
        staff = get_user_model().objects.create_user(username="desk", password="pass1234", role="STAFF")
        self.client.force_authenticate(staff)
        response = self.client.get(f"/api/v1/tickets/pdf/{self.ticket.ticket_number}/")
        self.assertEqual(response.status_code, 200)

    def test_lookup_returns_download_token(self):
        response = self.client.get(
            "/api/v1/bookings/lookup/",
            {"booking_reference": self.booking.booking_reference, "payment_phone": str(self.booking.payment_phone)},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data["tickets"][0]["download_token"],
            ticket_download_token(self.ticket.ticket_number),
        )
