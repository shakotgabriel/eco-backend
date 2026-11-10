"""
URL routing for fraud detection APIs.
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from fraud_detection import views

router = DefaultRouter()
router.register(r'duplicate-alerts', views.DuplicateBookingAlertViewSet, basename='duplicate-alert')
router.register(r'scalping-alerts', views.SeatScalpingAlertViewSet, basename='scalping-alert')
router.register(r'fraud-queue', views.FraudAlertViewSet, basename='fraud-alert')
router.register(r'no-show-tracking', views.NoShowTrackingViewSet, basename='no-show')
router.register(r'seat-heat-maps', views.SeatHeatMapViewSet, basename='heat-map')
router.register(r'demand-forecasts', views.DemandForecastViewSet, basename='forecast')
router.register(r'payment-health', views.PaymentHealthCheckViewSet, basename='payment-health')

urlpatterns = [
    path('', include(router.urls)),
]
