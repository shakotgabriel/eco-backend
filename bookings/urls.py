from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import BookingViewSet, booking_lookup, multi_leg_booking

router = DefaultRouter()
router.register("", BookingViewSet)

urlpatterns = [
    path("lookup/", booking_lookup, name="booking_lookup"),
    path("multi-leg/", multi_leg_booking, name="multi_leg_booking"),
] + router.urls
