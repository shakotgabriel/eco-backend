import uuid

from bebeto_backend.base import BaseModel
from django.db import models
from django.utils import timezone


class ExchangeRate(BaseModel):
    """Currency exchange rates relative to USD."""
    CURRENCY_CHOICES = (
        ("SSP", "South Sudanese Pound"),
        ("KES", "Kenyan Shilling"),
        ("UGX", "Ugandan Shilling"),
        ("RWF", "Rwandan Franc"),
        ("USD", "US Dollar"),
    )
    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES, unique=True)
    rate_to_usd = models.DecimalField(
        max_digits=12, decimal_places=6,
        help_text="How many units of this currency equal 1 USD"
    )

    class Meta:
        ordering = ["currency"]

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        from .currency import invalidate_cache
        invalidate_cache()

    def __str__(self):
        return f"1 USD = {self.rate_to_usd} {self.currency}"


class Payment(BaseModel):
    METHOD_CHOICES = (
        ("CASH", "Cash"),
        ("MOBILE_MONEY", "Mobile Money"),
        ("MTN_MOMO", "MTN Mobile Money"),
        ("MPESA", "M-Pesa"),
        ("BANK_TRANSFER", "Bank Transfer"),
    )
    STATUS_CHOICES = (
        ("PENDING", "Pending"),
        ("COMPLETED", "Completed"),
        ("FAILED", "Failed"),
        ("REFUNDED", "Refunded"),
    )

    booking = models.ForeignKey(
        "bookings.Booking", on_delete=models.CASCADE, related_name="payments"
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default="SSP", help_text="Currency the customer pays in")
    original_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text="Amount before currency conversion")
    original_currency = models.CharField(max_length=3, blank=True, help_text="Route's native currency")
    exchange_rate = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)
    method = models.CharField(max_length=20, choices=METHOD_CHOICES)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default="PENDING")
    transaction_ref = models.CharField(max_length=100, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["transaction_ref"], name="idx_payment_txn_ref"),
            models.Index(fields=["booking", "status"], name="idx_payment_booking_status"),
            models.Index(fields=["method", "status"], name="idx_payment_method_status"),
        ]

    def save(self, *args, **kwargs):
        if self.status == "COMPLETED" and not self.paid_at:
            self.paid_at = timezone.now()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Payment {self.id} - {self.method} ({self.status})"


class Refund(BaseModel):
    """An auditable refund instruction or completed cash refund for a payment."""
    STATUS_CHOICES = (
        ("PENDING", "Pending"),
        ("COMPLETED", "Completed"),
        ("FAILED", "Failed"),
    )
    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="refunds")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3)
    method = models.CharField(max_length=20, choices=Payment.METHOD_CHOICES)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default="PENDING")
    reason = models.TextField(blank=True)
    reference = models.CharField(max_length=100, blank=True)
    processed_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="processed_refunds",
    )
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["payment", "status"], name="idx_refund_payment_status")]

    def save(self, *args, **kwargs):
        if self.status == "COMPLETED" and not self.processed_at:
            self.processed_at = timezone.now()
        super().save(*args, **kwargs)


class MoMoTransaction(BaseModel):
    """Tracks MTN MoMo-specific transaction details alongside a Payment."""
    MOMO_STATUS_CHOICES = (
        ("PENDING", "Pending"),
        ("SUCCESSFUL", "Successful"),
        ("FAILED", "Failed"),
        ("REJECTED", "Rejected"),
        ("TIMEOUT", "Timeout"),
        ("EXPIRED", "Expired"),
    )

    payment = models.OneToOneField(
        Payment, on_delete=models.CASCADE, related_name="momo_transaction"
    )
    reference_id = models.UUIDField(
        unique=True, db_index=True,
        help_text="X-Reference-Id sent to MTN MoMo API"
    )
    external_id = models.CharField(
        max_length=100,
        help_text="Our internal reference sent to MTN (e.g. booking ref)"
    )
    payer_msisdn = models.CharField(
        max_length=15,
        help_text="Payer phone number in MSISDN format (e.g. 256771234567)"
    )
    momo_status = models.CharField(
        max_length=15, choices=MOMO_STATUS_CHOICES, default="PENDING"
    )
    financial_transaction_id = models.CharField(
        max_length=100, blank=True,
        help_text="MTN's financial transaction ID (set on success)"
    )
    reason = models.TextField(
        blank=True,
        help_text="Failure reason returned by MTN"
    )
    status_checked_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Last time we polled MTN for status"
    )
    callback_received_at = models.DateTimeField(
        null=True, blank=True,
        help_text="When we received the callback from MTN"
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "MoMo Transaction"
        verbose_name_plural = "MoMo Transactions"

    def __str__(self):
        return f"MoMo {self.reference_id} ({self.momo_status})"


class MpesaTransaction(BaseModel):
    """Tracks Safaricom M-Pesa Daraja STK Push transaction details."""
    MPESA_STATUS_CHOICES = (
        ("PENDING", "Pending"),
        ("SUCCESSFUL", "Successful"),
        ("FAILED", "Failed"),
        ("CANCELLED", "Cancelled"),
        ("TIMEOUT", "Timeout"),
    )

    payment = models.OneToOneField(
        Payment, on_delete=models.CASCADE, related_name="mpesa_transaction"
    )
    merchant_request_id = models.CharField(
        max_length=100, db_index=True,
        help_text="MerchantRequestID from Safaricom STK Push response"
    )
    checkout_request_id = models.CharField(
        max_length=100, unique=True, db_index=True,
        help_text="CheckoutRequestID from Safaricom STK Push response"
    )
    phone_number = models.CharField(
        max_length=15,
        help_text="Payer phone number in 254XXXXXXXXX format"
    )
    mpesa_status = models.CharField(
        max_length=15, choices=MPESA_STATUS_CHOICES, default="PENDING"
    )
    mpesa_receipt_number = models.CharField(
        max_length=50, blank=True,
        help_text="M-Pesa receipt number (set on success)"
    )
    result_code = models.IntegerField(
        null=True, blank=True,
        help_text="ResultCode from Safaricom callback (0 = success)"
    )
    result_desc = models.TextField(
        blank=True,
        help_text="ResultDesc from Safaricom callback"
    )
    callback_received_at = models.DateTimeField(
        null=True, blank=True,
        help_text="When we received the callback from Safaricom"
    )
    status_checked_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Last time we queried Safaricom for status"
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "M-Pesa Transaction"
        verbose_name_plural = "M-Pesa Transactions"

    def __str__(self):
        return f"M-Pesa {self.checkout_request_id} ({self.mpesa_status})"


class IdempotencyKey(models.Model):
    """Prevents duplicate payment requests. Each key can only be used once."""
    key = models.CharField(max_length=255, unique=True, db_index=True)
    endpoint = models.CharField(max_length=200, help_text="API endpoint path")
    request_hash = models.CharField(max_length=64, help_text="SHA-256 of the request body")
    response_status = models.IntegerField()
    response_body = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Idempotency Key"
        verbose_name_plural = "Idempotency Keys"
        indexes = [
            models.Index(fields=["key", "endpoint"]),
        ]

    def __str__(self):
        return f"{self.key} ({self.endpoint})"
