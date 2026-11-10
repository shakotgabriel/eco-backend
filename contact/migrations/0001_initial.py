# Generated migration for contact app

from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='ContactMessage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255)),
                ('email', models.EmailField(max_length=254)),
                ('phone', models.CharField(blank=True, max_length=20, null=True)),
                ('subject', models.CharField(max_length=255)),
                ('message', models.TextField()),
                ('status', models.CharField(choices=[('NEW', 'New'), ('ACKNOWLEDGED', 'Acknowledged'), ('RESOLVED', 'Resolved')], default='NEW', max_length=20)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='contactmessage',
            index=models.Index(fields=['status', '-created_at'], name='contact_con_status_idx'),
        ),
        migrations.AddIndex(
            model_name='contactmessage',
            index=models.Index(fields=['email'], name='contact_con_email_idx'),
        ),
    ]
