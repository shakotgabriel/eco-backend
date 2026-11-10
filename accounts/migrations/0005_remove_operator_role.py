from django.db import migrations, models


def convert_operators_to_managers(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    for user in User.objects.filter(role="OPERATOR"):
        user.role = "MANAGER"
        user.is_staff = True
        if user.username == "operator.kigali":
            if not User.objects.filter(username="manager.kigali").exclude(pk=user.pk).exists():
                user.username = "manager.kigali"
            if user.email == "operator.kigali@bebetocoach.com":
                user.email = "manager.kigali@bebetocoach.com"
        user.save(update_fields=["role", "is_staff", "username", "email"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_portalsettings"),
    ]

    operations = [
        migrations.RunPython(convert_operators_to_managers, noop_reverse),
        migrations.AlterField(
            model_name="user",
            name="role",
            field=models.CharField(
                choices=[
                    ("ADMIN", "Admin"),
                    ("MANAGER", "Manager"),
                    ("STAFF", "Staff"),
                    ("DRIVER", "Driver"),
                ],
                default="STAFF",
                max_length=10,
            ),
        ),
    ]
