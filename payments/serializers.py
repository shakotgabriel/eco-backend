from rest_framework import serializers

from .models import ExchangeRate, MoMoTransaction, MpesaTransaction, Payment, Refund


class PaymentSerializer(serializers.ModelSerializer):
    booking_reference = serializers.CharField(source="booking.booking_reference", read_only=True)

    class Meta:
        model = Payment
        fields = [
            "id", "booking", "booking_reference",
            "amount", "currency", "original_amount", "original_currency", "exchange_rate",
            "method", "status", "transaction_ref", "paid_at",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "paid_at", "created_at", "updated_at"]


class RefundSerializer(serializers.ModelSerializer):
    payment_reference = serializers.CharField(source="payment.transaction_ref", read_only=True)

    class Meta:
        model = Refund
        fields = [
            "id", "payment", "payment_reference", "amount", "currency", "method",
            "status", "reason", "reference", "processed_by", "processed_at",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "processed_by", "processed_at", "created_at", "updated_at"]


class ExchangeRateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExchangeRate
        fields = ["id", "currency", "rate_to_usd", "updated_at"]
        read_only_fields = ["id", "updated_at"]


class CurrencyConvertSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    from_currency = serializers.CharField(max_length=3)
    to_currency = serializers.CharField(max_length=3)


# ── MTN MoMo Serializers ────────────────────────────────────────────────


class MoMoRequestToPaySerializer(serializers.Serializer):
    """Input for initiating an MTN MoMo payment."""
    booking_id = serializers.IntegerField(help_text="ID of the booking to pay for")
    phone_number = serializers.CharField(
        max_length=15,
        help_text="Payer's MTN Uganda (+256) or MTN South Sudan (+211) number, e.g. 256771234567"
    )

    def validate_phone_number(self, value):
        from .mtn_momo import MARKET_SOUTH_SUDAN, is_market_configured, market_for_msisdn

        msisdn = value.strip().lstrip("+")
        market = market_for_msisdn(msisdn)
        if market is None:
            raise serializers.ValidationError("Use an MTN Uganda (+256) or MTN South Sudan (+211) number.")
        if len(msisdn) < 12 or len(msisdn) > 13:
            raise serializers.ValidationError("Invalid phone number length.")
        if market == MARKET_SOUTH_SUDAN and not is_market_configured(market):
            raise serializers.ValidationError(
                "MTN MoMo South Sudan isn't available yet. Pay with cash at an EcoBus office or choose another method."
            )
        return msisdn


# ── M-Pesa Daraja Serializers ─────────────────────────────────────────


class MpesaSTKPushSerializer(serializers.Serializer):
    """Input for initiating an M-Pesa STK Push payment."""
    booking_id = serializers.IntegerField(help_text="ID of the booking to pay for")
    phone_number = serializers.CharField(
        max_length=15,
        help_text="Payer's Safaricom number (e.g. +254712345678 or 254712345678)"
    )

    def validate_phone_number(self, value):
        msisdn = value.lstrip("+")
        if not msisdn.startswith("254"):
            raise serializers.ValidationError("Phone number must be a Kenyan number starting with 254.")
        if len(msisdn) < 12 or len(msisdn) > 13:
            raise serializers.ValidationError("Invalid Kenyan phone number length.")
        return msisdn


class MpesaTransactionSerializer(serializers.ModelSerializer):
    booking_reference = serializers.CharField(
        source="payment.booking.booking_reference", read_only=True
    )
    payment_status = serializers.CharField(source="payment.status", read_only=True)
    amount = serializers.DecimalField(
        source="payment.amount", max_digits=10, decimal_places=2, read_only=True
    )
    currency = serializers.CharField(source="payment.currency", read_only=True)

    class Meta:
        model = MpesaTransaction
        fields = [
            "id", "merchant_request_id", "checkout_request_id", "phone_number",
            "mpesa_status", "mpesa_receipt_number", "result_code", "result_desc",
            "booking_reference", "payment_status", "amount", "currency",
            "callback_received_at", "status_checked_at",
            "created_at", "updated_at",
        ]
        read_only_fields = fields


class MoMoTransactionSerializer(serializers.ModelSerializer):
    booking_reference = serializers.CharField(
        source="payment.booking.booking_reference", read_only=True
    )
    payment_status = serializers.CharField(source="payment.status", read_only=True)
    amount = serializers.DecimalField(
        source="payment.amount", max_digits=10, decimal_places=2, read_only=True
    )
    currency = serializers.CharField(source="payment.currency", read_only=True)

    class Meta:
        model = MoMoTransaction
        fields = [
            "id", "reference_id", "external_id", "payer_msisdn",
            "momo_status", "financial_transaction_id", "reason",
            "booking_reference", "payment_status", "amount", "currency",
            "status_checked_at", "callback_received_at",
            "created_at", "updated_at",
        ]
        read_only_fields = fields
