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
DASHBOARD_HOST="${GLOBALID_DASHBOARD_HOST:-0.0.0.0}"
DASHBOARD_PORT="${GLOBALID_DASHBOARD_PORT:-3000}"

export API_PROXY_TARGET="${API_PROXY_TARGET:-http://${API_PROXY_HOST}:${API_PORT}}"

cleanup_old_dashboard_releases() {
  local release_root="$1"
  local active_release="$2"
  local retain="${GLOBALID_DASHBOARD_RELEASES_RETAIN:-5}"

  if [[ ! "$retain" =~ ^[0-9]+$ ]] || (( retain < 1 )); then
    return
  fi

  local kept=0
  local entry
  local release_dir
  while IFS= read -r entry; do
    release_dir="${entry#* }"
    if [[ "$release_dir" == "$active_release" ]]; then
      kept=$((kept + 1))
      continue
    fi
    if (( kept < retain )); then
      kept=$((kept + 1))
      continue
    fi
    rm -rf -- "$release_dir"
  done < <(
    find "$release_root" -mindepth 1 -maxdepth 1 -type d ! -name '.staging.*' -printf '%T@ %p\n' \
      | sort -nr
  )
}

cd "$DASHBOARD_DIR"

needs_build=0
if [[ ! -f ".next/BUILD_ID" ]]; then
  needs_build=1
elif is_truthy "${GLOBALID_DASHBOARD_BUILD_ON_START:-0}"; then
  needs_build=1
elif find \
  src \
  public \
  -type f \
  -newer ".next/BUILD_ID" \
  -print \
  -quit 2>/dev/null | grep -q .; then
  needs_build=1
elif find \
  package.json \
  package-lock.json \
  next.config.ts \
  postcss.config.mjs \
  tsconfig.json \
  -maxdepth 0 \
  -type f \
  -newer ".next/BUILD_ID" \
  -print \
  -quit 2>/dev/null | grep -q .; then
  needs_build=1
fi

if [[ "$needs_build" == "1" ]]; then
  "$NPM_BIN" run build
fi

if [[ -f ".next/standalone/server.js" ]]; then
  build_id="$(<.next/BUILD_ID)"
  release_root="$LOG_DIR/dashboard-web-releases"
  release_dir="$release_root/$build_id"

  mkdir -p "$release_root"

  if [[ ! -f "$release_dir/server.js" ]]; then
    staged_release="$(mktemp -d "$release_root/.staging.XXXXXX")"
    trap 'rm -rf "${staged_release:-}"' EXIT

    cp -a ".next/standalone/." "$staged_release/"
    mkdir -p "$staged_release/.next"
    cp -a ".next/static" "$staged_release/.next/static"

    if [[ -d "public" ]]; then
      cp -a "public" "$staged_release/public"
    fi

    mv "$staged_release" "$release_dir"
    staged_release=""
    trap - EXIT
  fi

  cleanup_old_dashboard_releases "$release_root" "$release_dir"

  export HOSTNAME="$DASHBOARD_HOST"
  export PORT="$DASHBOARD_PORT"
  exec "$NODE_BIN" "$release_dir/server.js"
fi

exec "$NPM_BIN" run start -- --hostname "$DASHBOARD_HOST" --port "$DASHBOARD_PORT"
