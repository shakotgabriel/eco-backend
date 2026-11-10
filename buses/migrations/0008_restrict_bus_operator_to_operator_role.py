import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0007_restore_driver_operator_roles"),
        ("buses", "0007_bus_custom_seat_layout"),
    ]

    operations = [
        migrations.AlterField(
            model_name="bus",
            name="operator",
            field=models.ForeignKey(
                blank=True,
                help_text="Operator who manages this bus (not the trip driver)",
                limit_choices_to={"role": "OPERATOR"},
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="operated_buses",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
