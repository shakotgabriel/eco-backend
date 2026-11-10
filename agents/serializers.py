from decimal import Decimal

from rest_framework import serializers

from .models import Agent, AgentPayout, AgentSale, CommissionTier


class CommissionTierSerializer(serializers.ModelSerializer):
    class Meta:
        model = CommissionTier
        fields = ["id", "name", "min_monthly_sales", "commission_pct"]


class AgentSerializer(serializers.ModelSerializer):
    user_display = serializers.CharField(source="user.__str__", read_only=True)
    city_name = serializers.CharField(source="city.name", read_only=True, default=None)
    city_country = serializers.CharField(source="city.country", read_only=True, default=None)
    currency = serializers.SerializerMethodField()
    tier_name = serializers.CharField(source="current_tier.name", read_only=True, default=None)
    effective_commission_pct = serializers.DecimalField(
        max_digits=5, decimal_places=2, read_only=True
    )
    referred_by_code = serializers.CharField(
        source="referred_by.agent_code", read_only=True, default=None
    )
    referral_count = serializers.SerializerMethodField()

    class Meta:
        model = Agent
        fields = [
            "id", "user", "user_display", "agent_code", "business_name",
            "city", "city_name", "city_country", "currency", "phone",
            "commission_pct", "current_tier", "tier_name", "effective_commission_pct",
            "referred_by", "referred_by_code", "referral_count",
            "monthly_sales_target",
            "status", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "effective_commission_pct", "created_at", "updated_at"]

    def get_referral_count(self, obj):
        return obj.referrals.count()

    def get_currency(self, obj):
        country = (obj.city.country if obj.city else "") or ""
        mapping = {
            "kenya": "KES",
            "uganda": "UGX",
            "south sudan": "SSP",
            "rwanda": "RWF",
        }
        return mapping.get(country.strip().lower(), "")


class AgentSaleSerializer(serializers.ModelSerializer):
    agent_code = serializers.CharField(source="agent.agent_code", read_only=True)
    booking_reference = serializers.CharField(source="booking.booking_reference", read_only=True)
    payout_id = serializers.IntegerField(source="payout.id", read_only=True, default=None)

    class Meta:
        model = AgentSale
        fields = [
            "id", "agent", "agent_code", "booking", "booking_reference",
            "commission_pct", "commission_amount", "is_paid", "paid_at",
            "payout_id", "created_at",
        ]
        read_only_fields = ["id", "commission_pct", "commission_amount", "created_at"]


class AgentPayoutSerializer(serializers.ModelSerializer):
    agent_code = serializers.CharField(source="agent.agent_code", read_only=True)
    paid_by_name = serializers.SerializerMethodField()

    class Meta:
        model = AgentPayout
        fields = [
            "id", "agent", "agent_code", "amount", "currency",
            "method", "status", "reference", "note",
            "paid_by", "paid_by_name",
            "sales_count", "period_from", "period_to",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "amount", "sales_count", "period_from", "period_to",
            "created_at", "updated_at",
        ]

    def get_paid_by_name(self, obj):
        if obj.paid_by:
            return obj.paid_by.get_full_name() or obj.paid_by.username
        return None


class AgentCommissionSummarySerializer(serializers.Serializer):
    """Read-only summary for commission reports."""
    total_sales = serializers.IntegerField()
    total_revenue = serializers.DecimalField(max_digits=12, decimal_places=2)
    total_commission = serializers.DecimalField(max_digits=12, decimal_places=2)
    unpaid_commission = serializers.DecimalField(max_digits=12, decimal_places=2)
    current_tier = serializers.CharField(allow_null=True)
    effective_commission_pct = serializers.DecimalField(max_digits=5, decimal_places=2)
    monthly_sales = serializers.IntegerField()
    currency = serializers.CharField()


class AgentSelfSerializer(serializers.ModelSerializer):
    """Read-only serializer for agent's own profile (self-service portal)."""
    city_name = serializers.CharField(source="city.name", read_only=True, default=None)
    tier_name = serializers.CharField(source="current_tier.name", read_only=True, default=None)
    effective_commission_pct = serializers.DecimalField(
        max_digits=5, decimal_places=2, read_only=True
    )
    referral_count = serializers.SerializerMethodField()

    class Meta:
        model = Agent
        fields = [
            "id", "agent_code", "business_name", "city_name", "phone",
            "commission_pct", "tier_name", "effective_commission_pct",
            "referral_count", "status", "created_at",
        ]

    def get_referral_count(self, obj):
        return obj.referrals.count()


class CreatePayoutSerializer(serializers.Serializer):
    """Input for creating a payout for an agent."""
    method = serializers.ChoiceField(choices=["CASH", "BANK_TRANSFER", "MTN_MOMO", "MPESA"])
    reference = serializers.CharField(max_length=100, required=False, default="")
    note = serializers.CharField(required=False, default="")
