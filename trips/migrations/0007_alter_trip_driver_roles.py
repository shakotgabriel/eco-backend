import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0006_remove_driver_role"),
        ("trips", "0006_trip_idx_trip_status_departure_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="trip",
            name="driver",
            field=models.ForeignKey(
                blank=True,
                help_text="Staff member assigned to drive this trip",
                limit_choices_to={"role": "STAFF"},
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
                help_text="Default staff member assigned to drive trips from this schedule",
                limit_choices_to={"role": "STAFF"},
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="scheduled_drives",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
