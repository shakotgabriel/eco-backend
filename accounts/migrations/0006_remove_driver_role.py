from django.db import migrations, models


def convert_drivers_to_staff(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    rename_map = {
        "driver.john": ("staff.john", "staff.john@bebetocoach.com"),
        "driver.peter": ("staff.peter", "staff.peter@bebetocoach.com"),
        "driver.mary": ("staff.mary", "staff.mary@bebetocoach.com"),
        "driver.grace": ("staff.grace", "staff.grace@bebetocoach.com"),
    }
    for user in User.objects.filter(role="DRIVER"):
        user.role = "STAFF"
        if user.username in rename_map:
            new_username, new_email = rename_map[user.username]
            if not User.objects.filter(username=new_username).exclude(pk=user.pk).exists():
                user.username = new_username
            if user.email.startswith("driver."):
                user.email = new_email
        user.save(update_fields=["role", "username", "email"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0005_remove_operator_role"),
    ]

    operations = [
        migrations.RunPython(convert_drivers_to_staff, noop_reverse),
        migrations.AlterField(
            model_name="user",
            name="role",
            field=models.CharField(
                choices=[
                    ("ADMIN", "Admin"),
                    ("MANAGER", "Manager"),
                    ("STAFF", "Staff"),
                ],
                default="STAFF",
                max_length=10,
            ),
        ),
    ]
