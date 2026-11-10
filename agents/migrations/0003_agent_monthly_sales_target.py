# Generated manually for monthly_sales_target

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("agents", "0002_commissiontier_agent_referred_by_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="agent",
            name="monthly_sales_target",
            field=models.PositiveIntegerField(
                default=0,
                help_text="Monthly ticket sales target set by management",
            ),
        ),
    ]
