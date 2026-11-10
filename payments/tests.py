from decimal import Decimal
from unittest import mock

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from bebeto_backend.testing import make_booking, make_trip
from payments.models import Payment


def _credentials(booking):
    return {"booking_reference": booking.booking_reference, "payment_phone": str(booking.payment_phone)}


class MpesaInitiationIdempotencyTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.booking = make_booking(make_trip(), status="PENDING")

    def _push(self, headers=None):
        body = {"booking_id": self.booking.pk, "phone_number": "254712345678", **_credentials(self.booking)}
        return self.client.post("/api/v1/payments/mpesa/stk-push/", body, format="json", headers=headers or {})

    def test_paid_booking_is_not_charged_again(self):
        Payment.objects.create(
            booking=self.booking, amount=Decimal("10000.00"), currency="KES", method="MPESA", status="COMPLETED"
        )
        with mock.patch("payments.views.get_mpesa_client") as client:
            response = self._push()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["already_paid"])
        client.assert_not_called()
        self.assertEqual(self.booking.payments.count(), 1)

    def test_open_prompt_blocks_second_charge(self):
        Payment.objects.create(
            booking=self.booking, amount=Decimal("10000.00"), currency="KES", method="MPESA", status="PENDING"
        )
        with mock.patch("payments.views.get_mpesa_client") as client:
            response = self._push()
        self.assertEqual(response.status_code, 409)
        client.assert_not_called()
        self.assertEqual(self.booking.payments.count(), 1)


class MomoMarketTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.booking = make_booking(make_trip(currency="SSP"), status="PENDING", phone="+211912345678")

    @override_settings(MTN_MOMO_SS_API_USER="")
    def test_south_sudan_number_rejected_until_configured(self):
        body = {"booking_id": self.booking.pk, "phone_number": "211912345678", **_credentials(self.booking)}
        response = self.client.post("/api/v1/payments/momo/request-to-pay/", body, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("phone_number", response.data)

    @override_settings(
        MTN_MOMO_SS_API_USER="user",
        MTN_MOMO_SS_COLLECTION_SECRET="secret",
        MTN_MOMO_SS_COLLECTION_PRIMARY_KEY="key",
        MTN_MOMO_SS_CURRENCY="SSP",
    )
    def test_south_sudan_number_collects_in_ssp(self):
        from payments.mtn_momo import MARKET_SOUTH_SUDAN, collection_currency_for_msisdn, market_for_msisdn

        self.assertEqual(market_for_msisdn("+211912345678"), MARKET_SOUTH_SUDAN)
        self.assertEqual(collection_currency_for_msisdn("211912345678"), "SSP")


class PaymentRetryAccessTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.booking = make_booking(make_trip(), status="PENDING")
        self.failed = Payment.objects.create(
            booking=self.booking, amount=Decimal("10000.00"), currency="KES", method="MPESA", status="FAILED"
        )

    def test_payment_id_alone_is_not_enough(self):
        response = self.client.post("/api/v1/payments/retry/", {"payment_id": self.failed.pk}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_retry_is_idempotent(self):
        body = {"payment_id": self.failed.pk, **_credentials(self.booking)}
        first = self.client.post("/api/v1/payments/retry/", body, format="json")
        second = self.client.post("/api/v1/payments/retry/", body, format="json")
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 400)
        self.assertEqual(self.booking.payments.filter(status="PENDING").count(), 1)
