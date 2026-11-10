from django.contrib import admin

from .models import ExchangeRate


@admin.register(ExchangeRate)
class ExchangeRateAdmin(admin.ModelAdmin):
    list_display = [
        "from_currency", "to_currency", "rate",
        "effective_date", "updated_by", "updated_at",
    ]
    list_filter = ["from_currency", "to_currency", "effective_date"]
    search_fields = ["from_currency", "to_currency", "notes"]
    ordering = ["-effective_date", "from_currency", "to_currency"]
    readonly_fields = ["updated_by", "created_at", "updated_at"]

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)
