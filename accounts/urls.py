from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    UserViewSet,
    change_password,
    confirm_password_reset,
    me,
    portal_settings,
    public_payment_methods,
    request_password_reset,
    update_me,
)

router = DefaultRouter()
router.register("users", UserViewSet, basename="user")

urlpatterns = [
    path("me/", me, name="user_me"),
    path("me/update/", update_me, name="user_me_update"),
    path("portal-settings/", portal_settings, name="portal_settings"),
    path("payment-methods/", public_payment_methods, name="public_payment_methods"),
    path("change-password/", change_password, name="change_password"),
    path("password-reset/", request_password_reset, name="password_reset"),
    path("password-reset-confirm/", confirm_password_reset, name="password_reset_confirm"),
] + router.urls
