from rest_framework.routers import DefaultRouter

from .views import ExchangeRateViewSet

router = DefaultRouter()
router.register("", ExchangeRateViewSet)

urlpatterns = router.urls
