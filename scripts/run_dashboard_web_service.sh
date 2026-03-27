#!/usr/bin/env bash

set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/service_common.sh"

ensure_log_dir

DASHBOARD_DIR="$ROOT_DIR/dashboard"
require_dir "$DASHBOARD_DIR" "dashboard directory not found."
require_dir "$DASHBOARD_DIR/node_modules" "dashboard/node_modules not found. Run 'cd dashboard && npm install' first."

NPM_BIN="$(resolve_npm)"
NODE_BIN="$(resolve_node)"
API_PORT="${GLOBALID_API_PORT:-8000}"
API_PROXY_HOST="${GLOBALID_API_PROXY_HOST:-127.0.0.1}"
WS_HOST="${GLOBALID_WS_HOST:-localhost}"
DASHBOARD_HOST="${GLOBALID_DASHBOARD_HOST:-0.0.0.0}"
DASHBOARD_PORT="${GLOBALID_DASHBOARD_PORT:-3000}"

export NEXT_PUBLIC_API_URL="${NEXT_PUBLIC_API_URL:-/api/v1}"
export API_PROXY_TARGET="${API_PROXY_TARGET:-http://${API_PROXY_HOST}:${API_PORT}}"
export NEXT_PUBLIC_WS_URL="${NEXT_PUBLIC_WS_URL:-ws://${WS_HOST}:${API_PORT}/api/v1}"

cd "$DASHBOARD_DIR"

if [[ ! -f ".next/BUILD_ID" ]] || is_truthy "${GLOBALID_DASHBOARD_BUILD_ON_START:-0}"; then
  "$NPM_BIN" run build
fi

if [[ -f ".next/standalone/server.js" ]]; then
  export HOSTNAME="$DASHBOARD_HOST"
  export PORT="$DASHBOARD_PORT"
  exec "$NODE_BIN" ".next/standalone/server.js"
fi

exec "$NPM_BIN" run start -- --hostname "$DASHBOARD_HOST" --port "$DASHBOARD_PORT"
