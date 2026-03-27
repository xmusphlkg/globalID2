#!/usr/bin/env bash

set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/service_common.sh"

ensure_log_dir

SITE_DIR="$ROOT_DIR/astro-site"
DIST_DIR="$SITE_DIR/dist"
SITE_HOST="${GLOBALID_SITE_HOST:-0.0.0.0}"
SITE_PORT="${GLOBALID_SITE_PORT:-4321}"
SITE_BUILD_REQUIRED=0

require_dir "$SITE_DIR" "astro-site directory not found."

PYTHON_BIN="$(resolve_python)"

if is_truthy "${GLOBALID_SITE_REGENERATE_ON_START:-0}"; then
  cd "$ROOT_DIR"
  "$PYTHON_BIN" scripts/generate_site_data.py
  SITE_BUILD_REQUIRED=1
fi

if [[ ! -f "$DIST_DIR/index.html" ]] || is_truthy "${GLOBALID_SITE_BUILD_ON_START:-0}"; then
  SITE_BUILD_REQUIRED=1
fi

if [[ "$SITE_BUILD_REQUIRED" == "1" ]]; then
  require_dir "$SITE_DIR/node_modules" "astro-site/node_modules not found. Run 'cd astro-site && npm install' first."
  NPM_BIN="$(resolve_npm)"
  cd "$SITE_DIR"
  "$NPM_BIN" run build
fi

cd "$ROOT_DIR"

exec "$PYTHON_BIN" -m http.server "$SITE_PORT" --bind "$SITE_HOST" --directory "$DIST_DIR"
