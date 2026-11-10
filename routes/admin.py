from django.contrib import admin

from .models import Route, RoutePriceHistory


class RoutePriceHistoryInline(admin.TabularInline):
    model = RoutePriceHistory
    extra = 0
    readonly_fields = ("old_price", "new_price", "old_vip_price", "new_vip_price", "currency", "changed_by", "reason", "created_at")


@admin.register(Route)
class RouteAdmin(admin.ModelAdmin):
    list_display = ("origin", "destination", "distance_km", "estimated_duration", "base_price", "vip_price", "currency", "allowed_bus_types", "is_active")
    list_filter = ("currency", "is_active", "origin", "destination")
    search_fields = ("origin__name", "destination__name")
    list_editable = ("is_active", "base_price", "vip_price")
    autocomplete_fields = ("origin", "destination")
    filter_horizontal = ("assigned_buses",)
    inlines = [RoutePriceHistoryInline]


@admin.register(RoutePriceHistory)
class RoutePriceHistoryAdmin(admin.ModelAdmin):
    list_display = ("route", "old_price", "new_price", "old_vip_price", "new_vip_price", "currency", "changed_by", "reason", "created_at")
    list_filter = ("route", "changed_by")
    readonly_fields = ("route", "old_price", "new_price", "old_vip_price", "new_vip_price", "currency", "changed_by", "reason", "created_at")
