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

resolve_node() {
  if have_command node; then
    command -v node
    return
  fi

  echo "node not found. Install Node.js first." >&2
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

port_listener_pid() {
  local port="$1"
  if have_command lsof; then
    lsof -ti tcp:"$port" -sTCP:LISTEN 2>/dev/null | head -n 1 || true
    return
  fi
  if have_command ss; then
    ss -ltnpH "( sport = :$port )" 2>/dev/null \
      | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' \
      | head -n 1 || true
  fi
}

require_port_available() {
  local port="$1"
  local service_name="$2"
  local listener_pid
  listener_pid="$(port_listener_pid "$port")"

  if [[ -n "$listener_pid" ]]; then
    local command_line
    command_line="$(ps -p "$listener_pid" -o args= 2>/dev/null || true)"
    echo "$service_name cannot start: port $port is already owned by PID $listener_pid (${command_line:-unknown command})." >&2
    echo "Stop the standalone process or the competing service; do not run scripts/dashboard.sh and systemd for the same stack." >&2
    exit 1
  fi

  # Some ss builds omit process data for listeners owned by another user.
  if have_command ss && ss -ltnH "( sport = :$port )" 2>/dev/null | grep -q ":$port"; then
    echo "$service_name cannot start: port $port already has a listener." >&2
    exit 1
  fi
}

require_no_python_module_process() {
  local module="$1"
  local service_name="$2"
  local cmdline
  local pid
  local arg
  for cmdline in /proc/[0-9]*/cmdline; do
    [[ -r "$cmdline" ]] || continue
    while IFS= read -r -d '' arg; do
      if [[ "$arg" == "$module" ]]; then
        pid="${cmdline#/proc/}"
        pid="${pid%/cmdline}"
        echo "$service_name cannot start: $module is already running as PID $pid." >&2
        echo "Use exactly one service owner (systemd or scripts/dashboard.sh), then retry." >&2
        exit 1
      fi
    done < "$cmdline"
  done
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
