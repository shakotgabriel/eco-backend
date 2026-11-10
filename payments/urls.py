from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    ExchangeRateViewSet,
    PaymentViewSet,
    currency_convert,
    mobile_money_callback,
    momo_callback,
    momo_check_status,
    momo_request_to_pay,
    momo_validate_account,
    mpesa_callback,
    mpesa_check_status,
    mpesa_stk_push,
)
from .payment_retry_views import retry_payment, get_payment_retry_history
from .payment_dashboard import PaymentDashboardViewSet

router = DefaultRouter()
router.register("exchange-rates", ExchangeRateViewSet, basename="exchangerate")
router.register("", PaymentViewSet)
router.register("dashboard", PaymentDashboardViewSet, basename="payment-dashboard")

urlpatterns = [
    path("callbacks/mobile-money/", mobile_money_callback, name="mobile_money_callback"),
    path("currency/convert/", currency_convert, name="currency_convert"),
    # MTN MoMo Uganda
    path("momo/request-to-pay/", momo_request_to_pay, name="momo_request_to_pay"),
    path("momo/status/<uuid:reference_id>/", momo_check_status, name="momo_check_status"),
    path("momo/callback/", momo_callback, name="momo_callback"),
    path("momo/validate-account/", momo_validate_account, name="momo_validate_account"),
    # M-Pesa Daraja (Safaricom Kenya)
    path("mpesa/stk-push/", mpesa_stk_push, name="mpesa_stk_push"),
    path("mpesa/status/<str:checkout_request_id>/", mpesa_check_status, name="mpesa_check_status"),
    path("mpesa/callback/", mpesa_callback, name="mpesa_callback"),
    # Payment retry & retry history
    path("retry/", retry_payment, name="retry_payment"),
    path("<int:payment_id>/retry-history/", get_payment_retry_history, name="payment_retry_history"),
] + router.urls
