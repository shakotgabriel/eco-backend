from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    AuditLogViewSet,
    ReportViewSet,
    dashboard_agent_leaderboard,
    dashboard_city_performance,
    dashboard_fleet_status,
    dashboard_manager_performance,
    dashboard_recent_transactions,
    dashboard_revenue_trend,
    dashboard_route_performance,
    dashboard_sales_report,
    dashboard_stats,
)

router = DefaultRouter()
router.register("audit-logs", AuditLogViewSet, basename="audit-log")
router.register("", ReportViewSet)

urlpatterns = [
    path("dashboard/", dashboard_stats, name="dashboard_stats"),
    path("dashboard/revenue-trend/", dashboard_revenue_trend, name="dashboard_revenue_trend"),
    path("dashboard/route-performance/", dashboard_route_performance, name="dashboard_route_performance"),
    path("dashboard/recent-transactions/", dashboard_recent_transactions, name="dashboard_recent_transactions"),
    path("dashboard/fleet/", dashboard_fleet_status, name="dashboard_fleet_status"),
    path("dashboard/agent-leaderboard/", dashboard_agent_leaderboard, name="dashboard_agent_leaderboard"),
    path("dashboard/city-performance/", dashboard_city_performance, name="dashboard_city_performance"),
    path("dashboard/manager-performance/", dashboard_manager_performance, name="dashboard_manager_performance"),
    path("dashboard/sales-report/", dashboard_sales_report, name="dashboard_sales_report"),
] + router.urls
