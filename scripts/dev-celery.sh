#!/usr/bin/env bash
# Start Celery worker + beat for local development.
# Uses the project venv (required — system Python may lack redis-py).
#
#   ./scripts/dev-celery.sh
#   ./scripts/dev-celery.sh worker
#   ./scripts/dev-celery.sh beat

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -x "$ROOT/venv/bin/celery" ]]; then
  CELERY="$ROOT/venv/bin/celery"
elif [[ -x "$ROOT/.venv/bin/celery" ]]; then
  CELERY="$ROOT/.venv/bin/celery"
else
  echo "Project venv not found. Create it and: pip install -r requirements.txt"
  exit 1
fi

export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-bebeto_backend.settings}"

if ! redis-cli ping >/dev/null 2>&1; then
  echo "Redis is not responding on localhost:6379."
  echo "Start it first, e.g.: brew services start redis"
  exit 1
fi

mode="${1:-all}"
WORKER_ARGS=(-A bebeto_backend worker -l INFO --concurrency="${CELERY_CONCURRENCY:-2}")
BEAT_ARGS=(-A bebeto_backend beat -l INFO --scheduler "${CELERY_BEAT_SCHEDULER:-celery.beat:PersistentScheduler}")

case "$mode" in
  worker)
    echo "Starting Celery worker with $CELERY"
    exec "$CELERY" "${WORKER_ARGS[@]}"
    ;;
  beat)
    echo "Starting Celery beat with $CELERY"
    exec "$CELERY" "${BEAT_ARGS[@]}"
    ;;
  all)
    echo "Starting Celery worker + beat with $CELERY"
    "$CELERY" "${WORKER_ARGS[@]}" &
    WORKER_PID=$!
    "$CELERY" "${BEAT_ARGS[@]}" &
    BEAT_PID=$!
    echo "worker pid=$WORKER_PID  beat pid=$BEAT_PID"
    echo "Ctrl+C stops both."
    trap 'kill $WORKER_PID $BEAT_PID 2>/dev/null; wait' INT TERM
    wait
    ;;
  *)
    echo "Usage: $0 [all|worker|beat]"
    exit 1
    ;;
esac
