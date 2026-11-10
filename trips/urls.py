from rest_framework.routers import DefaultRouter

from .views import TripScheduleViewSet, TripViewSet

router = DefaultRouter()
router.register("schedules", TripScheduleViewSet)
router.register("", TripViewSet, basename="trip")

urlpatterns = router.urls
