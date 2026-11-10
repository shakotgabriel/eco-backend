from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import ParcelViewSet, parcel_track

router = DefaultRouter()
router.register("", ParcelViewSet)

urlpatterns = [
    path("track/<str:tracking_number>/", parcel_track, name="parcel_track"),
] + router.urls
