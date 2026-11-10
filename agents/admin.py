from django.contrib import admin

from .models import Agent, AgentPayout, AgentSale, CommissionTier


class AgentSaleInline(admin.TabularInline):
    model = AgentSale
    extra = 0
    readonly_fields = ["booking", "commission_pct", "commission_amount", "is_paid", "paid_at", "payout"]


@admin.register(CommissionTier)
class CommissionTierAdmin(admin.ModelAdmin):
    list_display = ("name", "min_monthly_sales", "commission_pct")
    ordering = ("min_monthly_sales",)


@admin.register(Agent)
class AgentAdmin(admin.ModelAdmin):
    list_display = ("agent_code", "business_name", "user", "city", "commission_pct", "current_tier", "referred_by", "status")
    list_filter = ("status", "city", "current_tier")
    search_fields = ("agent_code", "business_name", "user__username", "phone")
    raw_id_fields = ("user", "referred_by")
    inlines = [AgentSaleInline]


@admin.register(AgentSale)
class AgentSaleAdmin(admin.ModelAdmin):
    list_display = ("agent", "booking", "commission_pct", "commission_amount", "is_paid", "paid_at", "payout", "created_at")
    list_filter = ("is_paid", "agent")
    raw_id_fields = ("agent", "booking", "payout")


@admin.register(AgentPayout)
class AgentPayoutAdmin(admin.ModelAdmin):
    list_display = ("agent", "amount", "currency", "method", "status", "sales_count", "paid_by", "created_at")
    list_filter = ("status", "method", "agent")
    search_fields = ("agent__agent_code", "reference")
    raw_id_fields = ("agent", "paid_by")
