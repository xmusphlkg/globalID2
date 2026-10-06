#!/usr/bin/env bash

set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/service_common.sh"

ensure_log_dir

PYTHON_BIN="$(resolve_python)"
API_HOST="${GLOBALID_API_HOST:-0.0.0.0}"
API_PORT="${GLOBALID_API_PORT:-8000}"
API_GRACEFUL_SHUTDOWN_SECONDS="${GLOBALID_API_GRACEFUL_SHUTDOWN_SECONDS:-10}"

require_port_available "$API_PORT" "GlobalID dashboard API"

cd "$ROOT_DIR"
export PYTHONUNBUFFERED=1

exec "$PYTHON_BIN" -m uvicorn dashboard.api.main:app \
    --host "$API_HOST" \
    --port "$API_PORT" \
    --timeout-graceful-shutdown "$API_GRACEFUL_SHUTDOWN_SECONDS"
