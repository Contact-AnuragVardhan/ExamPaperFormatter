#!/usr/bin/env bash
set -euo pipefail

# One Gunicorn worker is intentional while WhatsApp/session/index state is
# stored in local JSON files. Threads allow concurrent HTTP requests without
# introducing multiple worker processes that could race on those files.
exec gunicorn \
  --bind "0.0.0.0:${PORT:-10000}" \
  --workers "${GUNICORN_WORKERS:-1}" \
  --threads "${GUNICORN_THREADS:-4}" \
  --timeout "${GUNICORN_TIMEOUT:-300}" \
  --log-level "${GUNICORN_LOG_LEVEL:-info}" \
  --capture-output \
  --access-logfile - \
  --error-logfile - \
  web.app:app
