#!/usr/bin/env bash

set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/service_common.sh"

ensure_log_dir

PYTHON_BIN="$(resolve_python)"

cd "$ROOT_DIR"
export PYTHONUNBUFFERED=1

exec "$PYTHON_BIN" -m src.control_plane.scheduler
