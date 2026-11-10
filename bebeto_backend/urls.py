"""
URL configuration for bebeto_backend project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from accounts.views import EmailOrUsernameTokenObtainPairView, logout_view
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.db import connection
from django.http import JsonResponse
from django.urls import include, path
from rest_framework_simplejwt.views import TokenRefreshView
from fraud_detection.agent_leaderboard import agent_leaderboard, agent_detail_drill_down


def healthz(_request):
    """Lightweight liveness/readiness probe for load balancers."""
    try:
        connection.ensure_connection()
        db_ok = True
    except Exception:
        db_ok = False
    status = 200 if db_ok else 503
    return JsonResponse({"status": "ok" if db_ok else "degraded", "database": db_ok}, status=status)


urlpatterns = [
    path("healthz/", healthz, name="healthz"),
    path("bebeto-staff-portal/", admin.site.urls),

    # JWT auth — accepts email or username in the `username` field.
    path("api/v1/auth/login/", EmailOrUsernameTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/v1/auth/token/", EmailOrUsernameTokenObtainPairView.as_view(), name="token_obtain_pair_legacy"),
    path("api/v1/auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("api/v1/auth/logout/", logout_view, name="token_logout"),

    # API v1
    path("api/v1/accounts/", include("accounts.urls")),
    path("api/v1/cities/", include("cities.urls")),
    path("api/v1/buses/", include("buses.urls")),
    path("api/v1/seats/", include("seats.urls")),
    path("api/v1/routes/", include("routes.urls")),
    path("api/v1/trips/", include("trips.urls")),
    path("api/v1/bookings/", include("bookings.urls")),
    path("api/v1/tickets/", include("tickets.urls")),
    path("api/v1/payments/", include("payments.urls")),
    path("api/v1/reports/", include("reports.urls")),
    path("api/v1/parcels/", include("parcels.urls")),
    path("api/v1/agents/", include("agents.urls")),
    path("api/v1/exchange-rates/", include("exchange_rates.urls")),
    path("api/v1/contact/", include("contact.urls")),
    
    # Fraud Detection & Analytics
    path("api/v1/fraud/", include("fraud_detection.urls")),
    path("api/v1/analytics/agent-leaderboard/", agent_leaderboard, name="agent_leaderboard"),
    path("api/v1/analytics/agent/<int:agent_id>/drill-down/", agent_detail_drill_down, name="agent_drill_down"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
