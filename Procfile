# Process types for Heroku-style platforms.
# Render free Blueprint uses bin/start-render.sh (API+worker+beat in one web service).

web: gunicorn bebeto_backend.wsgi:application --bind 0.0.0.0:$PORT --workers 3 --timeout 60
worker: celery -A bebeto_backend worker -l INFO --concurrency=2
beat: celery -A bebeto_backend beat -l INFO --scheduler django_celery_beat.schedulers:DatabaseScheduler
