#!/bin/sh
# Free Render web service: API + Celery in one process group (workers are paid).
# 512 MB total — keep gunicorn and the worker at 1 concurrency.
set -eu

# Free tier has no pre-deploy step — migrate on boot, then first admin.
python manage.py migrate --noinput
python manage.py ensure_admin
# Free tier has no Shell — seed catalog once when the DB is empty.
python manage.py seed_if_empty

celery -A bebeto_backend worker -l INFO --concurrency=1 --pool=solo &
celery -A bebeto_backend beat -l INFO \
  --scheduler django_celery_beat.schedulers:DatabaseScheduler &

exec gunicorn bebeto_backend.wsgi:application \
  --bind "0.0.0.0:${PORT:-8000}" \
  --workers 1 \
  --timeout 60
