import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0005_remove_operator_role"),
        ("buses", "0005_bus_current_mileage_km_maintenancerecord"),
    ]

    operations = [
        migrations.AlterField(
            model_name="bus",
            name="operator",
            field=models.ForeignKey(
                blank=True,
                help_text="Staff or manager who manages this bus",
                limit_choices_to={"role__in": ("ADMIN", "MANAGER", "STAFF")},
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="operated_buses",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
