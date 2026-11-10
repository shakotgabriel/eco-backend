"""
Export application data to CSV or JSON files.

Usage:
  python manage.py export_data                       # export all models as CSV
  python manage.py export_data --format json         # export all as JSON
  python manage.py export_data --models bookings trips  # specific apps only
  python manage.py export_data --output /tmp/backup  # custom output dir
"""
import csv
import json
import os
from datetime import date, datetime
from decimal import Decimal

from django.apps import apps
from django.core.management.base import BaseCommand
from django.core.serializers import serialize


EXPORTABLE_APPS = [
    "accounts", "cities", "buses", "routes", "trips", "seats",
    "bookings", "payments", "tickets", "reports", "parcels", "agents",
]


class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return str(o)
        if isinstance(o, (datetime, date)):
            return o.isoformat()
        return super().default(o)


class Command(BaseCommand):
    help = "Export application data to CSV or JSON files for backup"

    def add_arguments(self, parser):
        parser.add_argument(
            "--format", choices=["csv", "json"], default="csv",
            help="Output format (default: csv)",
        )
        parser.add_argument(
            "--models", nargs="*", default=None,
            help="App labels to export (default: all). E.g. --models bookings trips",
        )
        parser.add_argument(
            "--output", default=None,
            help="Output directory (default: ./exports/YYYY-MM-DD/)",
        )

    def handle(self, *args, **options):
        fmt = options["format"]
        app_labels = options["models"] or EXPORTABLE_APPS
        today = date.today().isoformat()
        output_dir = options["output"] or os.path.join("exports", today)

        os.makedirs(output_dir, exist_ok=True)
        self.stdout.write(f"Exporting to {output_dir}/ as {fmt.upper()}...\n")

        total_records = 0

        for app_label in app_labels:
            try:
                app_config = apps.get_app_config(app_label)
            except LookupError:
                self.stderr.write(f"  App '{app_label}' not found, skipping.")
                continue

            for model in app_config.get_models():
                model_name = model.__name__
                qs = model.objects.all()
                count = qs.count()

                if count == 0:
                    continue

                filename = f"{app_label}_{model_name.lower()}"

                if fmt == "json":
                    self._export_json(qs, model, output_dir, filename)
                else:
                    self._export_csv(qs, model, output_dir, filename)

                total_records += count
                self.stdout.write(f"  {app_label}.{model_name}: {count} records")

        self.stdout.write(self.style.SUCCESS(
            f"\nDone! Exported {total_records} records to {output_dir}/"
        ))

    def _export_json(self, qs, model, output_dir, filename):
        filepath = os.path.join(output_dir, f"{filename}.json")
        data = serialize("json", qs, indent=2)
        with open(filepath, "w") as f:
            f.write(data)

    def _export_csv(self, qs, model, output_dir, filename):
        filepath = os.path.join(output_dir, f"{filename}.csv")

        # Get concrete field names
        fields = [f.name for f in model._meta.get_fields() if hasattr(f, "column")]

        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(fields)

            for obj in qs.iterator(chunk_size=500):
                row = []
                for field_name in fields:
                    val = getattr(obj, field_name, "")
                    if isinstance(val, (datetime, date)):
                        val = val.isoformat()
                    elif isinstance(val, Decimal):
                        val = str(val)
                    row.append(val)
                writer.writerow(row)
