from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("buses", "0008_restrict_bus_operator_to_operator_role"),
    ]

    operations = [
        migrations.AddField(
            model_name="bus",
            name="insurance_expiry",
            field=models.DateField(blank=True, help_text="Date the insurance cover ends", null=True),
        ),
        migrations.AddField(
            model_name="bus",
            name="road_licence_expiry",
            field=models.DateField(blank=True, help_text="Date the road licence ends", null=True),
        ),
        migrations.AddField(
            model_name="bus",
            name="inspection_expiry",
            field=models.DateField(blank=True, help_text="Date the roadworthiness inspection ends", null=True),
        ),
    ]
