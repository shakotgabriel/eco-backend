from django.contrib import admin

from .models import ExchangeRate, IdempotencyKey, MoMoTransaction, MpesaTransaction, Payment


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("id", "booking", "amount", "currency", "method", "status", "transaction_ref", "paid_at", "created_at")
    list_filter = ("method", "status", "currency", "created_at")
    search_fields = ("booking__booking_reference", "transaction_ref")
    list_editable = ("status",)
    readonly_fields = ("paid_at",)


@admin.register(ExchangeRate)
class ExchangeRateAdmin(admin.ModelAdmin):
    list_display = ("currency", "rate_to_usd", "updated_at")
    list_editable = ("rate_to_usd",)


@admin.register(MoMoTransaction)
class MoMoTransactionAdmin(admin.ModelAdmin):
    list_display = (
        "reference_id", "payment", "payer_msisdn", "momo_status",
        "financial_transaction_id", "created_at",
    )
    list_filter = ("momo_status", "created_at")
    search_fields = ("reference_id", "external_id", "payer_msisdn", "financial_transaction_id")
    readonly_fields = (
        "payment", "reference_id", "external_id", "payer_msisdn",
        "financial_transaction_id", "reason", "status_checked_at", "callback_received_at",
    )


@admin.register(MpesaTransaction)
class MpesaTransactionAdmin(admin.ModelAdmin):
    list_display = (
        "checkout_request_id", "payment", "phone_number", "mpesa_status",
        "mpesa_receipt_number", "result_code", "created_at",
    )
    list_filter = ("mpesa_status", "created_at")
    search_fields = (
        "checkout_request_id", "merchant_request_id",
        "phone_number", "mpesa_receipt_number",
    )
    readonly_fields = (
        "payment", "merchant_request_id", "checkout_request_id",
        "phone_number", "mpesa_receipt_number", "result_code",
        "result_desc", "status_checked_at", "callback_received_at",
    )


@admin.register(IdempotencyKey)
class IdempotencyKeyAdmin(admin.ModelAdmin):
    list_display = ("key", "endpoint", "response_status", "created_at")
    list_filter = ("endpoint", "response_status")
    search_fields = ("key", "endpoint")
    readonly_fields = ("key", "endpoint", "request_hash", "response_status", "response_body")
