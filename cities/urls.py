from rest_framework.routers import DefaultRouter

from .views import CityViewSet

router = DefaultRouter()
router.register("", CityViewSet)

urlpatterns = router.urls
