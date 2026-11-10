from rest_framework.routers import DefaultRouter

from .views import BusViewSet, MaintenanceRecordViewSet

router = DefaultRouter()
router.register("maintenance", MaintenanceRecordViewSet, basename="maintenance")
router.register("", BusViewSet, basename="bus")

urlpatterns = router.urls
