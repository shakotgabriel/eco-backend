from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from agents.models import Agent, AgentSale
from bookings.models import Booking
from buses.models import Bus
from cities.models import City
from payments.models import Payment
from routes.models import Route
from trips.models import Trip


class ReportsDashboardApiTests(TestCase):
	@classmethod
	def setUpTestData(cls):
		user_model = get_user_model()
		cls.manager = user_model.objects.create_user(
			username="manager1",
			password="pass1234",
			role="MANAGER",
		)
		cls.staff = user_model.objects.create_user(
			username="staff1",
			password="pass1234",
			role="STAFF",
		)
		cls.driver = user_model.objects.create_user(
			username="driver1",
			password="pass1234",
			role="DRIVER",
			first_name="Driver",
			last_name="One",
		)
		cls.agent_user = user_model.objects.create_user(
			username="agent1",
			password="pass1234",
			role="STAFF",
			first_name="Jane",
			last_name="Agent",
		)

		nairobi = City.objects.create(name="Nairobi", code="NBO", country="Kenya")
		kampala = City.objects.create(name="Kampala", code="KLA", country="Uganda")

		active_route = Route.objects.create(
			origin=nairobi,
			destination=kampala,
			distance_km=Decimal("660.00"),
			estimated_duration=timedelta(hours=10),
			base_price=Decimal("5000.00"),
			currency="KES",
			is_active=True,
		)
		Route.objects.create(
			origin=kampala,
			destination=nairobi,
			distance_km=Decimal("660.00"),
			estimated_duration=timedelta(hours=10),
			base_price=Decimal("120000.00"),
			currency="UGX",
			is_active=False,
		)

		bus = Bus.objects.create(
			plate_number="KBZ123A",
			name="Bebeto Star",
			home_city=nairobi,
			capacity=48,
			bus_type="COMFORT_CRUISER",
			seat_layout="2+2",
			total_rows=12,
			regular_seats=48,
			vip_seats=0,
			status="ACTIVE",
		)

		now = timezone.now()
		trip = Trip.objects.create(
			route=active_route,
			bus=bus,
			driver=cls.driver,
			departure_time=now - timedelta(hours=1),
			arrival_time=now + timedelta(hours=8),
			regular_price=Decimal("5000.00"),
			status="DEPARTED",
			available_seats=44,
		)

		booking = Booking.objects.create(
			trip=trip,
			payer_name="Michael Doe",
			payment_phone="+254712345678",
			number_of_seats=2,
			total_price=Decimal("10000.00"),
			status="CONFIRMED",
		)

		Payment.objects.create(
			booking=booking,
			amount=Decimal("10000.00"),
			currency="KES",
			method="MPESA",
			status="COMPLETED",
			paid_at=now,
		)

		agent = Agent.objects.create(
			user=cls.agent_user,
			agent_code="AGT-NBO-001",
			business_name="Nairobi CBD",
			city=nairobi,
			phone="+254711000111",
			commission_pct=Decimal("5.00"),
			status="ACTIVE",
		)

		AgentSale.objects.create(
			agent=agent,
			booking=booking,
			commission_pct=Decimal("5.00"),
			commission_amount=Decimal("500.00"),
			is_paid=False,
		)

	def setUp(self):
		self.client = APIClient()

	def _auth_as(self, user):
		self.client.force_authenticate(user=user)

	def test_manager_can_access_dashboard_endpoints(self):
		self._auth_as(self.manager)

		endpoints = [
			"/api/v1/reports/dashboard/?period=7d",
			"/api/v1/reports/dashboard/revenue-trend/?period=7d",
			"/api/v1/reports/dashboard/route-performance/?period=30d&limit=5",
			"/api/v1/reports/dashboard/recent-transactions/?limit=5",
			"/api/v1/reports/dashboard/fleet/?limit=3",
			"/api/v1/reports/dashboard/agent-leaderboard/?period=7d&limit=3",
		]

		for endpoint in endpoints:
			response = self.client.get(endpoint)
			self.assertEqual(response.status_code, status.HTTP_200_OK, msg=endpoint)

	def test_dashboard_payload_contains_expected_sections(self):
		self._auth_as(self.manager)
		response = self.client.get("/api/v1/reports/dashboard/?period=7d")

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertIn("kpis", response.data)
		self.assertIn("revenue_trend", response.data)
		self.assertIn("route_performance", response.data)
		self.assertIn("agent_leaderboard", response.data)
		self.assertIn("fleet", response.data)
		self.assertIn("recent_transactions", response.data)

	def test_staff_is_forbidden_from_manager_dashboard_endpoints(self):
		self._auth_as(self.staff)
		response = self.client.get("/api/v1/reports/dashboard/")
		self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

	def test_recent_transactions_search_and_total(self):
		self._auth_as(self.manager)
		response = self.client.get("/api/v1/reports/dashboard/recent-transactions/", {"search": "Michael", "limit": 5})
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data["total"], 1)
		self.assertFalse(response.data["has_more"])

		miss = self.client.get("/api/v1/reports/dashboard/recent-transactions/", {"search": "nobody-here"})
		self.assertEqual(miss.data["total"], 0)

		second_page = self.client.get("/api/v1/reports/dashboard/recent-transactions/", {"page": 2, "limit": 1})
		self.assertEqual(second_page.data["results"], [])
