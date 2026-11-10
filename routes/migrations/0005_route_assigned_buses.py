from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("buses", "0006_alter_bus_operator_roles"),
        ("routes", "0004_routepricehistory"),
    ]

    operations = [
        migrations.AddField(
            model_name="route",
            name="assigned_buses",
            field=models.ManyToManyField(
                blank=True,
                help_text="Specific fleet buses that run this route. Empty = any bus of the allowed types.",
                related_name="assigned_routes",
                to="buses.bus",
            ),
        ),
    ]
