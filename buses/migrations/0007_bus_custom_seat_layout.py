from django.core.validators import RegexValidator
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("buses", "0006_alter_bus_operator_roles"),
    ]

    operations = [
        migrations.AlterField(
            model_name="bus",
            name="seat_layout",
            field=models.CharField(
                default="2+2",
                help_text="Seats left of the aisle + seats right of the aisle, e.g. 2+1 or 1+2.",
                max_length=5,
                validators=[
                    RegexValidator(
                        message="Use N+M with 1–4 seats on each side of the aisle, e.g. 1+2 or 2+3.",
                        regex=r"^[1-4]\+[1-4]$",
                    )
                ],
            ),
        ),
    ]
