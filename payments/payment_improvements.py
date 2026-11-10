"""
Additional models and utilities for payment improvement features.
Includes: payment health monitoring, webhook retry, dead-letter queue, etc.
"""
from django.db import models
from bebeto_backend.base import BaseModel


class PaymentProviderHealth(BaseModel):
    """Track the health status of payment providers (MTN MoMo, M-Pesa, etc.)."""
    PROVIDER_CHOICES = (
        ("MTN_MOMO", "MTN Mobile Money"),
        ("MPESA", "M-Pesa Daraja"),
        ("AT", "Africa's Talking"),
    )
    STATUS_CHOICES = (
        ("HEALTHY", "Healthy"),
        ("DEGRADED", "Degraded"),
        ("DOWN", "Down"),
    )

    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES, unique=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="HEALTHY")
    last_check_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_error_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True, help_text="Last error encountered")
    consecutive_failures = models.IntegerField(default=0)
    response_time_ms = models.IntegerField(null=True, blank=True, help_text="Last response time in milliseconds")

    class Meta:
        ordering = ["provider"]
        verbose_name = "Payment Provider Health"

    def __str__(self):
        return f"{self.provider} - {self.status}"


class WebhookRetry(BaseModel):
    """Track failed webhook deliveries and retry attempts."""
    WEBHOOK_TYPE_CHOICES = (
        ("MOMO_CALLBACK", "MTN MoMo Callback"),
        ("MPESA_CALLBACK", "M-Pesa Callback"),
        ("AT_CALLBACK", "Africa's Talking Callback"),
    )
    STATUS_CHOICES = (
        ("PENDING", "Pending"),
        ("DELIVERED", "Delivered"),
        ("FAILED", "Failed"),
        ("ABANDONED", "Abandoned"),
    )

    webhook_type = models.CharField(max_length=20, choices=WEBHOOK_TYPE_CHOICES)
    transaction_ref = models.CharField(max_length=200, help_text="Payment/Transaction reference")
    payload = models.JSONField(help_text="Original webhook payload")
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default="PENDING")
    attempt_count = models.IntegerField(default=0)
    max_attempts = models.IntegerField(default=5)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    next_retry_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["webhook_type", "status"], name="idx_webhook_retry_type_status"),
            models.Index(fields=["transaction_ref"], name="idx_webhook_retry_txn_ref"),
            models.Index(fields=["next_retry_at"], name="idx_webhook_retry_next_retry"),
        ]

    def __str__(self):
        return f"{self.webhook_type} - {self.transaction_ref} ({self.status})"


class FailedCeleryTask(BaseModel):
    """Dead-letter queue for failed Celery tasks."""
    task_name = models.CharField(max_length=255)
    args = models.JSONField(default=list)
    kwargs = models.JSONField(default=dict)
    error_message = models.TextField()
    traceback = models.TextField(blank=True)
    attempt_count = models.IntegerField(default=1)
    max_retries = models.IntegerField(default=3)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=15,
        choices=[("PENDING", "Pending"), ("RESOLVED", "Resolved"), ("ABANDONED", "Abandoned")],
        default="PENDING"
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["task_name", "status"], name="idx_failed_task_name_status"),
            models.Index(fields=["next_retry_at"], name="idx_failed_task_retry_at"),
        ]

    def __str__(self):
        return f"{self.task_name} - Attempt {self.attempt_count}/{self.max_retries}"


class CallbackIdempotencyKey(BaseModel):
    """Prevent duplicate webhook callbacks (separate from request idempotency)."""
    callback_id = models.CharField(max_length=255, unique=True, db_index=True, help_text="Callback ID from provider")
    webhook_type = models.CharField(max_length=20, help_text="Type of callback (MOMO, MPESA, etc.)")
    transaction_ref = models.CharField(max_length=200, help_text="Payment/Transaction reference")
    processed_at = models.DateTimeField(auto_now_add=True)
    response_status = models.IntegerField()
    response_body = models.JSONField()

    class Meta:
        ordering = ["-processed_at"]
        indexes = [
            models.Index(fields=["webhook_type", "transaction_ref"], name="idx_callback_idem_type_txn"),
        ]

    def __str__(self):
        return f"{self.webhook_type} - {self.callback_id}"


class PaymentRetryLog(BaseModel):
    """Track customer payment retry attempts."""
    payment = models.ForeignKey(
        "payments.Payment",
        on_delete=models.CASCADE,
        related_name="retry_logs"
    )
    original_attempt_at = models.DateTimeField(help_text="When the original payment was attempted")
    retry_attempt_number = models.IntegerField(default=1)
    attempted_at = models.DateTimeField(auto_now_add=True)
    new_payment_id = models.IntegerField(null=True, blank=True, help_text="ID of new Payment created on retry")
    status = models.CharField(
        max_length=15,
        choices=[("PENDING", "Pending"), ("SUCCEEDED", "Succeeded"), ("FAILED", "Failed")],
        default="PENDING"
    )

    class Meta:
        ordering = ["-attempted_at"]
        indexes = [
            models.Index(fields=["payment", "retry_attempt_number"], name="idx_retry_log_payment_attempt"),
        ]

    def __str__(self):
        return f"Retry #{self.retry_attempt_number} for Payment {self.payment_id}"
