#!/usr/bin/env bash

set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/service_common.sh"

ensure_log_dir

PYTHON_BIN="$(resolve_python)"

require_no_python_module_process "src.services.task_worker" "GlobalID task worker"

cd "$ROOT_DIR"
export PYTHONUNBUFFERED=1

exec "$PYTHON_BIN" -m src.services.task_worker
