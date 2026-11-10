import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0007_restore_driver_operator_roles"),
        ("trips", "0007_alter_trip_driver_roles"),
    ]

    operations = [
        migrations.AlterField(
            model_name="trip",
            name="driver",
            field=models.ForeignKey(
                blank=True,
                help_text="Driver assigned to this trip",
                limit_choices_to={"role": "DRIVER"},
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="driven_trips",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="tripschedule",
            name="driver",
            field=models.ForeignKey(
                blank=True,
                help_text="Default driver for trips generated from this schedule",
                limit_choices_to={"role": "DRIVER"},
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="scheduled_drives",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
