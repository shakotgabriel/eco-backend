from datetime import timedelta
from decimal import Decimal

from bebeto_backend.permissions import IsManagerOrAbove, IsStaffOrAbove
from django.db import models
from django.db.models import Avg, Count, F, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes as perm_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import Agent, AgentPayout, AgentSale, CommissionTier

# Fallback map for deriving a payout currency from an agent's home country when
# their sales history doesn't already give us an unambiguous currency.
COUNTRY_CURRENCY_MAP = {
    "kenya": "KES",
    "uganda": "UGX",
    "south sudan": "SSP",
    "rwanda": "RWF",
    "united states": "USD",
}


def _resolve_payout_currency(agent, sales_queryset):
    """Best-effort ISO currency code for an agent payout.

    Prefers the currency actually used by the agent's (unpaid) sales, since
    that's what the payout amount was aggregated in. Falls back to a
    country->currency lookup based on the agent's home city, and finally to SSP.
    """
    sale_currency = (
        sales_queryset.values_list("booking__trip__route__currency", flat=True)
        .exclude(booking__trip__route__currency__isnull=True)
        .exclude(booking__trip__route__currency="")
        .first()
    )
    if sale_currency:
        return sale_currency

    if agent.city and agent.city.country:
        return COUNTRY_CURRENCY_MAP.get(agent.city.country.strip().lower(), "SSP")

    return "SSP"
from .serializers import (
    AgentCommissionSummarySerializer,
    AgentPayoutSerializer,
    AgentSaleSerializer,
    AgentSelfSerializer,
    AgentSerializer,
    CommissionTierSerializer,
    CreatePayoutSerializer,
)


class CommissionTierViewSet(viewsets.ModelViewSet):
    """Manage commission tiers. Manager+ only."""
    queryset = CommissionTier.objects.all()
    serializer_class = CommissionTierSerializer
    permission_classes = [IsManagerOrAbove]


class AgentViewSet(viewsets.ModelViewSet):
    queryset = Agent.objects.select_related("user", "city", "current_tier", "referred_by").all()
    serializer_class = AgentSerializer
    permission_classes = [IsManagerOrAbove]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["city", "status", "current_tier"]
    search_fields = ["agent_code", "business_name", "user__username", "phone"]
    ordering = ["agent_code"]

    @action(detail=True, methods=["get"], url_path="commission-summary")
    def commission_summary(self, request, pk=None):
        """Return commission summary for a specific agent."""
        agent = self.get_object()
        sales = agent.sales.all()
        data = {
            "total_sales": sales.count(),
            "total_revenue": sales.aggregate(t=Sum("booking__total_price"))["t"] or Decimal("0"),
            "total_commission": sales.aggregate(t=Sum("commission_amount"))["t"] or Decimal("0"),
            "unpaid_commission": sales.filter(is_paid=False).aggregate(t=Sum("commission_amount"))["t"] or Decimal("0"),
            "current_tier": str(agent.current_tier) if agent.current_tier else None,
            "effective_commission_pct": agent.effective_commission_pct,
            "monthly_sales": agent.get_monthly_sales_count(),
            "currency": _resolve_payout_currency(agent, sales),
        }
        return Response(AgentCommissionSummarySerializer(data).data)

    @action(detail=True, methods=["post"], url_path="create-payout")
    def create_payout(self, request, pk=None):
        """Create a payout for all unpaid commissions of this agent."""
        agent = self.get_object()
        serializer = CreatePayoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        unpaid_sales = agent.sales.filter(is_paid=False)
        if not unpaid_sales.exists():
            return Response(
                {"detail": "No unpaid commissions for this agent."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        total_amount = unpaid_sales.aggregate(t=Sum("commission_amount"))["t"] or Decimal("0")
        dates = unpaid_sales.aggregate(
            earliest=models.Min("created_at"),
            latest=models.Max("created_at"),
        )

        payout = AgentPayout.objects.create(
            agent=agent,
            amount=total_amount,
            currency=_resolve_payout_currency(agent, unpaid_sales),
            method=serializer.validated_data["method"],
            status="COMPLETED",
            reference=serializer.validated_data.get("reference", ""),
            note=serializer.validated_data.get("note", ""),
            paid_by=request.user,
            sales_count=unpaid_sales.count(),
            period_from=dates["earliest"].date() if dates["earliest"] else None,
            period_to=dates["latest"].date() if dates["latest"] else None,
        )

        now = timezone.now()
        unpaid_sales.update(is_paid=True, paid_at=now, payout=payout)

        return Response(AgentPayoutSerializer(payout).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="update-tier")
    def update_tier(self, request, pk=None):
        """Recalculate commission tier for this agent based on monthly sales."""
        agent = self.get_object()
        tier = agent.update_tier()
        return Response({
            "detail": f"Tier updated to {tier}" if tier else "No qualifying tier found.",
            "current_tier": str(tier) if tier else None,
            "effective_commission_pct": str(agent.effective_commission_pct),
            "monthly_sales": agent.get_monthly_sales_count(),
        })

    @action(detail=True, methods=["get"], url_path="performance")
    def performance(self, request, pk=None):
        """Agent performance stats: sales trend, top routes, avg booking value."""
        agent = self.get_object()
        days = int(request.query_params.get("days", 30))
        end_date = timezone.now().date()
        start_date = end_date - timedelta(days=days - 1)

        sales = agent.sales.filter(
            created_at__date__gte=start_date,
            created_at__date__lte=end_date,
        )

        # Sales trend (daily)
        trend = (
            sales.annotate(day=TruncDate("created_at"))
            .values("day")
            .annotate(count=Count("id"), revenue=Sum("booking__total_price"), commission=Sum("commission_amount"))
            .order_by("day")
        )

        # Top routes
        top_routes = (
            sales.values(
                "booking__trip__route__origin__name",
                "booking__trip__route__destination__name",
            )
            .annotate(count=Count("id"), revenue=Sum("booking__total_price"))
            .order_by("-count")[:5]
        )

        # Averages
        avg_booking = sales.aggregate(
            avg_value=Avg("booking__total_price"),
            avg_commission=Avg("commission_amount"),
        )

        return Response({
            "agent_code": agent.agent_code,
            "period": f"{days}d",
            "total_sales": sales.count(),
            "total_revenue": str(sales.aggregate(t=Sum("booking__total_price"))["t"] or 0),
            "total_commission": str(sales.aggregate(t=Sum("commission_amount"))["t"] or 0),
            "avg_booking_value": str(avg_booking["avg_value"] or 0),
            "avg_commission": str(avg_booking["avg_commission"] or 0),
            "trend": [
                {
                    "date": str(t["day"]),
                    "sales": t["count"],
                    "revenue": str(t["revenue"] or 0),
                    "commission": str(t["commission"] or 0),
                }
                for t in trend
            ],
            "top_routes": [
                {
                    "route": f"{r['booking__trip__route__origin__name']} → {r['booking__trip__route__destination__name']}",
                    "sales": r["count"],
                    "revenue": str(r["revenue"] or 0),
                }
                for r in top_routes
            ],
        })

    @action(detail=True, methods=["get"], url_path="referrals")
    def referrals(self, request, pk=None):
        """List agents referred by this agent."""
        agent = self.get_object()
        referred = agent.referrals.select_related("user", "city").all()
        return Response(AgentSerializer(referred, many=True).data)

    @action(detail=True, methods=["get"], url_path="payouts")
    def payout_history(self, request, pk=None):
        """List all payouts for this agent."""
        agent = self.get_object()
        payouts = agent.payouts.all()
        return Response(AgentPayoutSerializer(payouts, many=True).data)


class AgentSaleViewSet(viewsets.ModelViewSet):
    queryset = AgentSale.objects.select_related("agent", "booking", "payout").all()
    serializer_class = AgentSaleSerializer
    permission_classes = [IsStaffOrAbove]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["agent", "is_paid"]
    search_fields = ["agent__agent_code", "booking__booking_reference"]
    ordering = ["-created_at"]

    def perform_create(self, serializer):
        """Auto-calculate commission using effective (tier-aware) rate, then update tier."""
        agent = serializer.validated_data["agent"]
        booking = serializer.validated_data["booking"]
        commission_pct = agent.effective_commission_pct
        commission_amount = (booking.total_price * commission_pct / Decimal("100")).quantize(Decimal("0.01"))
        serializer.save(commission_pct=commission_pct, commission_amount=commission_amount)
        # Recalculate tier after each sale
        agent.update_tier()


class AgentPayoutViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only view of all payouts. Manager+ only."""
    queryset = AgentPayout.objects.select_related("agent__user", "agent__city", "paid_by").all()
    serializer_class = AgentPayoutSerializer
    permission_classes = [IsManagerOrAbove]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["agent", "status", "method"]
    search_fields = ["agent__agent_code", "reference"]
    ordering = ["-created_at"]


# ─── Agent Self-Service Endpoints ─────────────────────────────────────────


@api_view(["GET"])
@perm_classes([IsAuthenticated])
def agent_me(request):
    """Agent views their own profile."""
    try:
        agent = Agent.objects.select_related("city", "current_tier").get(user=request.user)
    except Agent.DoesNotExist:
        return Response({"detail": "You are not registered as an agent."}, status=status.HTTP_404_NOT_FOUND)
    return Response(AgentSelfSerializer(agent).data)


@api_view(["GET"])
@perm_classes([IsAuthenticated])
def agent_my_sales(request):
    """Agent views their own sales."""
    try:
        agent = Agent.objects.get(user=request.user)
    except Agent.DoesNotExist:
        return Response({"detail": "You are not registered as an agent."}, status=status.HTTP_404_NOT_FOUND)

    sales = AgentSale.objects.filter(agent=agent).select_related("booking").order_by("-created_at")

    is_paid = request.query_params.get("is_paid")
    if is_paid is not None:
        sales = sales.filter(is_paid=is_paid.lower() == "true")

    return Response(AgentSaleSerializer(sales, many=True).data)


@api_view(["GET"])
@perm_classes([IsAuthenticated])
def agent_my_commissions(request):
    """Agent views their own commission summary and payout history."""
    try:
        agent = Agent.objects.select_related("current_tier").get(user=request.user)
    except Agent.DoesNotExist:
        return Response({"detail": "You are not registered as an agent."}, status=status.HTTP_404_NOT_FOUND)

    sales = agent.sales.all()
    summary = {
        "total_sales": sales.count(),
        "total_revenue": str(sales.aggregate(t=Sum("booking__total_price"))["t"] or 0),
        "total_commission": str(sales.aggregate(t=Sum("commission_amount"))["t"] or 0),
        "unpaid_commission": str(sales.filter(is_paid=False).aggregate(t=Sum("commission_amount"))["t"] or 0),
        "current_tier": str(agent.current_tier) if agent.current_tier else None,
        "effective_commission_pct": str(agent.effective_commission_pct),
        "monthly_sales": agent.get_monthly_sales_count(),
        "currency": _resolve_payout_currency(agent, sales),
    }

    payouts = agent.payouts.order_by("-created_at")[:20]

    return Response({
        "summary": summary,
        "recent_payouts": AgentPayoutSerializer(payouts, many=True).data,
    })
