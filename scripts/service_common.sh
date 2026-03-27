#!/usr/bin/env bash

set -euo pipefail

readonly ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly LOG_DIR="$ROOT_DIR/logs"

have_command() {
  command -v "$1" >/dev/null 2>&1
}

ensure_log_dir() {
  mkdir -p "$LOG_DIR"
}

resolve_python() {
  if [[ -x "$ROOT_DIR/venv/bin/python" ]]; then
    echo "$ROOT_DIR/venv/bin/python"
    return
  fi

  if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
    echo "$ROOT_DIR/.venv/bin/python"
    return
  fi

  if have_command python3; then
    command -v python3
    return
  fi

  echo "python3 not found. Install Python first." >&2
  exit 1
}

resolve_npm() {
  if have_command npm; then
    command -v npm
    return
  fi

  echo "npm not found. Install Node.js and npm first." >&2
  exit 1
}

resolve_docker() {
  if have_command docker; then
    command -v docker
    return
  fi

  echo "docker not found. Install Docker first." >&2
  exit 1
}

require_dir() {
  local dir="$1"
  local message="$2"

  if [[ ! -d "$dir" ]]; then
    echo "$message" >&2
    exit 1
  fi
}

is_truthy() {
  local value="${1:-}"
  shopt -s nocasematch
  case "$value" in
    1|true|yes|on)
      shopt -u nocasematch
      return 0
      ;;
    *)
      shopt -u nocasematch
      return 1
      ;;
  esac
}
