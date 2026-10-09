#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
# Render mounts the persistent disk at runtime, so seeding belongs here.
export PYTHONUNBUFFERED=1
python bootstrap.py
# One Uvicorn process owns the shared SQLite workers.
REV2_LOG_LEVEL="${LOG_LEVEL:-INFO}"
exec python -m uvicorn server:app \
  --host 0.0.0.0 \
  --port "${PORT:-10000}" \
  --workers 1 \
  --log-level "${REV2_LOG_LEVEL,,}"
