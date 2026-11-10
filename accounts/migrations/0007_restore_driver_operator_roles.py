from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0006_remove_driver_role"),
    ]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="role",
            field=models.CharField(
                choices=[
                    ("ADMIN", "Admin"),
                    ("MANAGER", "Manager"),
                    ("STAFF", "Staff"),
                    ("DRIVER", "Driver"),
                    ("OPERATOR", "Operator"),
                ],
                default="STAFF",
                max_length=10,
            ),
        ),
    ]
