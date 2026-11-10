from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    AgentPayoutViewSet,
    AgentSaleViewSet,
    AgentViewSet,
    CommissionTierViewSet,
    agent_me,
    agent_my_commissions,
    agent_my_sales,
)

router = DefaultRouter()
router.register("tiers", CommissionTierViewSet)
router.register("payouts", AgentPayoutViewSet)
router.register("sales", AgentSaleViewSet)
router.register("", AgentViewSet)

urlpatterns = [
    path("me/", agent_me, name="agent_me"),
    path("me/sales/", agent_my_sales, name="agent_my_sales"),
    path("me/commissions/", agent_my_commissions, name="agent_my_commissions"),
] + router.urls
