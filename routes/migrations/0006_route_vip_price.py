from decimal import Decimal

from django.db import migrations, models


def backfill_vip_price(apps, schema_editor):
    Route = apps.get_model("routes", "Route")
    for route in Route.objects.all():
        if route.vip_price is None and route.base_price is not None:
            route.vip_price = (route.base_price * Decimal("1.20")).quantize(Decimal("0.01"))
            route.save(update_fields=["vip_price"])


class Migration(migrations.Migration):
    dependencies = [
        ("routes", "0005_route_assigned_buses"),
    ]

    operations = [
        migrations.AddField(
            model_name="route",
            name="vip_price",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text="VIP seat fare for this corridor. Null = not set (schedules can still override).",
                max_digits=10,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="routepricehistory",
            name="old_vip_price",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name="routepricehistory",
            name="new_vip_price",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.RunPython(backfill_vip_price, migrations.RunPython.noop),
    ]
