from bebeto_backend.permissions import IsManagerOrAbove
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .dashboard import (
    get_agent_leaderboard,
    get_city_performance,
    get_dashboard_stats,
    get_fleet_status,
    get_manager_performance,
    get_recent_transactions,
    get_revenue_trend,
    get_route_performance,
    get_sales_report,
)
from .models import Report
from .audit import AuditLog
from .serializers import AuditLogSerializer, ReportSerializer
from .services import GENERATORS


class ReportViewSet(viewsets.ModelViewSet):
    queryset = Report.objects.select_related("generated_by").all()
    serializer_class = ReportSerializer
    permission_classes = [IsManagerOrAbove]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["report_type", "generated_by"]
    search_fields = ["title"]
    ordering_fields = ["created_at", "date_from"]
    ordering = ["-created_at"]

    @action(detail=False, methods=["post"], url_path="generate")
    def generate(self, request):
        """
        Generate a report from real data.
        POST body: { "report_type": "DAILY_SALES", "date_from": "2026-04-01", "date_to": "2026-04-21" }
        Creates a Report record with the aggregated data in the `data` JSON field.
        """
        report_type = request.data.get("report_type")
        date_from = request.data.get("date_from")
        date_to = request.data.get("date_to")

        if not all([report_type, date_from, date_to]):
            return Response(
                {"detail": "report_type, date_from, and date_to are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        generator = GENERATORS.get(report_type)
        if not generator:
            valid = list(GENERATORS.keys())
            return Response(
                {"detail": f"Invalid report_type. Choose from: {valid}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        data = generator(date_from, date_to)

        report = Report.objects.create(
            title=f"{report_type.replace('_', ' ').title()} — {date_from} to {date_to}",
            report_type=report_type,
            generated_by=request.user,
            date_from=date_from,
            date_to=date_to,
            data=data,
        )

        return Response(ReportSerializer(report).data, status=status.HTTP_201_CREATED)


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only audit log — admin and manager access only."""
    queryset = AuditLog.objects.select_related("actor").all()
    serializer_class = AuditLogSerializer
    permission_classes = [IsManagerOrAbove]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["action", "actor", "target_model"]
    search_fields = ["target_id", "actor__username", "ip_address"]
    ordering_fields = ["created_at", "action"]
    ordering = ["-created_at"]


@api_view(["GET"])
@permission_classes([IsManagerOrAbove])
def dashboard_stats(request):
    """Quick summary stats for the staff dashboard."""
    period = request.query_params.get("period", "7d")
    data = get_dashboard_stats(period=period)
    return Response(data)


@api_view(["GET"])
@permission_classes([IsManagerOrAbove])
def dashboard_revenue_trend(request):
    period = request.query_params.get("period", "7d")
    return Response(get_revenue_trend(period=period))


@api_view(["GET"])
@permission_classes([IsManagerOrAbove])
def dashboard_route_performance(request):
    period = request.query_params.get("period", "30d")
    limit = int(request.query_params.get("limit", 10))
    return Response(get_route_performance(period=period, limit=max(1, min(limit, 50))))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def dashboard_recent_transactions(request):
    """
    Recent payments for the transactions desk.
    Admin/manager: all activity.
    Staff/agent: only sales attributed to the current user.
    """
    from django.utils.dateparse import parse_date

    params = request.query_params
    try:
        limit = int(params.get("limit", 15))
        page = int(params.get("page", 1))
    except (TypeError, ValueError):
        return Response({"detail": "limit and page must be numbers."}, status=400)
    limit = max(1, min(limit, 500))
    role = str(getattr(request.user, "role", "") or "").upper()
    for_user = request.user if role in ("STAFF", "AGENT") else None
    return Response(get_recent_transactions(
        limit=limit,
        for_user=for_user,
        page=page,
        search=params.get("search", ""),
        status=params.get("status", ""),
        method=params.get("method", ""),
        date_from=parse_date(params.get("date_from", "") or ""),
        date_to=parse_date(params.get("date_to", "") or ""),
    ))


@api_view(["GET"])
@permission_classes([IsManagerOrAbove])
def dashboard_fleet_status(request):
    limit = int(request.query_params.get("limit", 6))
    return Response(get_fleet_status(limit=max(1, min(limit, 50))))


@api_view(["GET"])
@permission_classes([IsManagerOrAbove])
def dashboard_agent_leaderboard(request):
    period = request.query_params.get("period", "7d")
    limit = int(request.query_params.get("limit", 5))
    return Response(get_agent_leaderboard(period=period, limit=max(1, min(limit, 50))))


@api_view(["GET"])
@permission_classes([IsManagerOrAbove])
def dashboard_city_performance(request):
    period = request.query_params.get("period", "30d")
    limit = int(request.query_params.get("limit", 20))
    return Response(get_city_performance(period=period, limit=max(1, min(limit, 50))))


@api_view(["GET"])
@permission_classes([IsManagerOrAbove])
def dashboard_manager_performance(request):
    period = request.query_params.get("period", "30d")
    limit = int(request.query_params.get("limit", 20))
    return Response(get_manager_performance(period=period, limit=max(1, min(limit, 50))))


@api_view(["GET"])
@permission_classes([IsManagerOrAbove])
def dashboard_sales_report(request):
    """
    Filtered sales for admin reporting.
    Query params: period, granularity (daily|weekly|monthly|yearly),
    country, city_id, route_id, currency, year, month, date_from, date_to
    """
    period = request.query_params.get("period", "7d")
    granularity = request.query_params.get("granularity", "daily")
    country = request.query_params.get("country") or None
    currency = request.query_params.get("currency") or None
    city_raw = request.query_params.get("city_id")
    route_raw = request.query_params.get("route_id")
    year_raw = request.query_params.get("year")
    month_raw = request.query_params.get("month")
    date_from = request.query_params.get("date_from") or None
    date_to = request.query_params.get("date_to") or None
    compare = request.query_params.get("compare") or None
    city_id = int(city_raw) if city_raw and str(city_raw).isdigit() else None
    route_id = int(route_raw) if route_raw and str(route_raw).isdigit() else None
    year = int(year_raw) if year_raw and str(year_raw).isdigit() else None
    month = int(month_raw) if month_raw and str(month_raw).isdigit() else None
    return Response(
        get_sales_report(
            period=period,
            granularity=granularity,
            country=country,
            city_id=city_id,
            route_id=route_id,
            currency=currency,
            year=year,
            month=month,
            date_from=date_from,
            date_to=date_to,
            compare=compare,
        )
    )
