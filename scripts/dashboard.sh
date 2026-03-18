#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DASHBOARD_DIR="$ROOT_DIR/dashboard"
LOG_DIR="$ROOT_DIR/logs"

API_LOG="$LOG_DIR/dashboard-api.log"
WEB_LOG="$LOG_DIR/dashboard-web.log"
WORKER_LOG="$LOG_DIR/dashboard-worker.log"
API_PID_FILE="$LOG_DIR/dashboard-api.pid"
WEB_PID_FILE="$LOG_DIR/dashboard-web.pid"
WORKER_PID_FILE="$LOG_DIR/dashboard-worker.pid"

ACTION="${1:-status}"
TARGET="${2:-all}"

mkdir -p "$LOG_DIR"

usage() {
  cat <<'EOF'
Usage:
  ./scripts/dashboard.sh start [all|api|worker|web]
  ./scripts/dashboard.sh stop [all|api|worker|web]
  ./scripts/dashboard.sh restart [all|api|worker|web]
  ./scripts/dashboard.sh status
  ./scripts/dashboard.sh logs [api|worker|web]

Examples:
  ./scripts/dashboard.sh start
  ./scripts/dashboard.sh start api
  ./scripts/dashboard.sh start worker
  DASHBOARD_API_RELOAD=1 ./scripts/dashboard.sh start api
  ./scripts/dashboard.sh stop web
  ./scripts/dashboard.sh logs api
EOF
}

have_command() {
  command -v "$1" >/dev/null 2>&1
}

port_has_listener() {
  local port="$1"

  if have_command lsof && lsof -ti tcp:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    return 0
  fi

  if have_command ss && ss -ltnH "( sport = :$port )" 2>/dev/null | grep -q ":$port"; then
    return 0
  fi

  return 1
}

find_port_pid() {
  local port="$1"
  if have_command lsof; then
    lsof -ti tcp:"$port" -sTCP:LISTEN 2>/dev/null | head -n 1 || true
  fi
}

pid_cmdline() {
  local pid="$1"
  ps -p "$pid" -o args= 2>/dev/null || true
}

is_managed_api_pid() {
  local pid="$1"
  local cmd
  cmd="$(pid_cmdline "$pid")"
  [[ "$cmd" == *"dashboard.api.main:app"* ]]
}

is_managed_worker_pid() {
  local pid="$1"
  local cmd
  cmd="$(pid_cmdline "$pid")"
  [[ "$cmd" == *"src.services.task_worker"* ]]
}

is_managed_web_pid() {
  local pid="$1"
  local cmd
  cmd="$(pid_cmdline "$pid")"
  [[ "$cmd" == *"dashboard/node_modules/.bin/next"* && "$cmd" == *"dev"* && "$cmd" == *"--port 3000"* ]]
}

find_worker_pid() {
  ps -eo pid=,args= 2>/dev/null | awk '/src\.services\.task_worker/ && !/awk/ {print $1; exit}'
}

find_web_pid() {
  local port_pid
  port_pid="$(find_port_pid 3000)"
  if [[ -n "$port_pid" ]] && is_managed_web_pid "$port_pid"; then
    echo "$port_pid"
    return
  fi
  ps -eo pid=,args= 2>/dev/null | awk '/dashboard\/node_modules\/\.bin\/next/ && /dev/ && /--port 3000/ && !/awk/ {print $1; exit}'
}

find_managed_service_pid() {
  local service="$1"
  case "$service" in
    api)
      local port_pid
      port_pid="$(find_port_pid 8000)"
      if [[ -n "$port_pid" ]] && is_managed_api_pid "$port_pid"; then
        echo "$port_pid"
      fi
      ;;
    worker)
      find_worker_pid
      ;;
    web)
      find_web_pid
      ;;
  esac
}

adopt_managed_pid_if_needed() {
  local service="$1"
  local pid_file="$2"
  local pid
  pid="$(read_pid "$pid_file")"
  if [[ -n "$pid" ]] && pid_is_running "$pid"; then
    return
  fi

  local managed_pid
  managed_pid="$(find_managed_service_pid "$service")"
  if [[ -n "$managed_pid" ]] && pid_is_running "$managed_pid"; then
    echo "$managed_pid" > "$pid_file"
  fi
}

pid_is_running() {
  local pid="$1"
  [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1
}

read_pid() {
  local pid_file="$1"
  if [[ -f "$pid_file" ]]; then
    tr -d '[:space:]' < "$pid_file"
  fi
}

cleanup_pid_file_if_stale() {
  local pid_file="$1"
  local pid
  pid="$(read_pid "$pid_file")"
  if [[ -n "$pid" ]] && ! pid_is_running "$pid"; then
    rm -f "$pid_file"
  fi
}

ensure_port_free() {
  local port="$1"
  if port_has_listener "$port"; then
    local port_pid
    port_pid="$(find_port_pid "$port")"
    if [[ -n "$port_pid" ]]; then
      echo "Port $port is already in use by PID $port_pid" >&2
    else
      echo "Port $port is already in use by another process" >&2
    fi
    exit 1
  fi
}

resolve_uvicorn() {
  if [[ -x "$ROOT_DIR/venv/bin/uvicorn" ]]; then
    echo "$ROOT_DIR/venv/bin/uvicorn"
    return
  fi
  if [[ -x "$ROOT_DIR/.venv/bin/uvicorn" ]]; then
    echo "$ROOT_DIR/.venv/bin/uvicorn"
    return
  fi
  if have_command uvicorn; then
    command -v uvicorn
    return
  fi

  echo "uvicorn not found. Install Python dependencies first." >&2
  exit 1
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

start_api() {
  adopt_managed_pid_if_needed "api" "$API_PID_FILE"
  cleanup_pid_file_if_stale "$API_PID_FILE"
  local pid
  pid="$(read_pid "$API_PID_FILE")"
  if [[ -n "$pid" ]] && pid_is_running "$pid"; then
    echo "API is already running (PID $pid)"
    return
  fi

  local port_pid
  port_pid="$(find_port_pid 8000)"
  if [[ -n "$port_pid" ]] && is_managed_api_pid "$port_pid"; then
    echo "$port_pid" > "$API_PID_FILE"
    echo "API is already running (PID $port_pid)"
    return
  fi

  ensure_port_free 8000

  local uvicorn_bin
  local api_reload
  local -a uvicorn_args
  uvicorn_bin="$(resolve_uvicorn)"
  api_reload="${DASHBOARD_API_RELOAD:-0}"

  uvicorn_args=(dashboard.api.main:app --host 0.0.0.0 --port 8000)
  if [[ "$api_reload" == "1" || "$api_reload" == "true" ]]; then
    uvicorn_args+=(--reload)
    echo "Starting API on http://localhost:8000 (reload enabled)"
  else
    echo "Starting API on http://localhost:8000"
  fi

  nohup "$uvicorn_bin" "${uvicorn_args[@]}" > "$API_LOG" 2>&1 &
  echo $! > "$API_PID_FILE"
  sleep 2

  pid="$(read_pid "$API_PID_FILE")"
  if [[ -n "$pid" ]] && pid_is_running "$pid"; then
    echo "API started (PID $pid)"
  else
    echo "API failed to start. Check $API_LOG" >&2
    rm -f "$API_PID_FILE"
    exit 1
  fi
}

start_web() {
  adopt_managed_pid_if_needed "web" "$WEB_PID_FILE"
  cleanup_pid_file_if_stale "$WEB_PID_FILE"
  local pid
  pid="$(read_pid "$WEB_PID_FILE")"
  if [[ -n "$pid" ]] && pid_is_running "$pid"; then
    echo "Dashboard web is already running (PID $pid)"
    return
  fi

  if [[ ! -d "$DASHBOARD_DIR/node_modules" ]]; then
    echo "dashboard/node_modules not found. Run 'cd dashboard && npm install' first." >&2
    exit 1
  fi

  local port_pid
  port_pid="$(find_port_pid 3000)"
  if [[ -n "$port_pid" ]] && is_managed_web_pid "$port_pid"; then
    echo "$port_pid" > "$WEB_PID_FILE"
    echo "Dashboard web is already running (PID $port_pid)"
    return
  fi

  ensure_port_free 3000

  echo "Starting dashboard web on http://localhost:3000"
  (
    cd "$DASHBOARD_DIR"
    nohup npm run dev -- --port 3000 > "$WEB_LOG" 2>&1 &
    echo $! > "$WEB_PID_FILE"
  )
  sleep 2

  pid="$(read_pid "$WEB_PID_FILE")"
  if [[ -n "$pid" ]] && pid_is_running "$pid"; then
    echo "Dashboard web started (PID $pid)"
  else
    echo "Dashboard web failed to start. Check $WEB_LOG" >&2
    rm -f "$WEB_PID_FILE"
    exit 1
  fi
}

start_worker() {
  adopt_managed_pid_if_needed "worker" "$WORKER_PID_FILE"
  cleanup_pid_file_if_stale "$WORKER_PID_FILE"
  local pid
  pid="$(read_pid "$WORKER_PID_FILE")"
  if [[ -n "$pid" ]] && pid_is_running "$pid"; then
    echo "Task worker is already running (PID $pid)"
    return
  fi

  local managed_pid
  managed_pid="$(find_managed_service_pid "worker")"
  if [[ -n "$managed_pid" ]] && pid_is_running "$managed_pid"; then
    echo "$managed_pid" > "$WORKER_PID_FILE"
    echo "Task worker is already running (PID $managed_pid)"
    return
  fi

  local python_bin
  python_bin="$(resolve_python)"

  echo "Starting task worker"
  (
    cd "$ROOT_DIR"
    nohup "$python_bin" -m src.services.task_worker > "$WORKER_LOG" 2>&1 &
    echo $! > "$WORKER_PID_FILE"
  )
  sleep 2

  pid="$(read_pid "$WORKER_PID_FILE")"
  if [[ -n "$pid" ]] && pid_is_running "$pid"; then
    echo "Task worker started (PID $pid)"
  else
    echo "Task worker failed to start. Check $WORKER_LOG" >&2
    rm -f "$WORKER_PID_FILE"
    exit 1
  fi
}

stop_one() {
  local name="$1"
  local pid_file="$2"
  local service="$3"

  adopt_managed_pid_if_needed "$service" "$pid_file"
  cleanup_pid_file_if_stale "$pid_file"
  local pid
  pid="$(read_pid "$pid_file")"
  if [[ -z "$pid" ]]; then
    pid="$(find_managed_service_pid "$service")"
    if [[ -n "$pid" ]] && pid_is_running "$pid"; then
      echo "$pid" > "$pid_file"
    else
      echo "$name is not running"
      return
    fi
  fi

  if ! pid_is_running "$pid"; then
    rm -f "$pid_file"
    echo "$name is not running"
    return
  fi

  echo "Stopping $name (PID $pid)"
  kill "$pid" >/dev/null 2>&1 || true

  for _ in {1..20}; do
    if ! pid_is_running "$pid"; then
      rm -f "$pid_file"
      echo "$name stopped"
      return
    fi
    sleep 0.5
  done

  echo "$name did not stop gracefully, sending SIGKILL"
  kill -9 "$pid" >/dev/null 2>&1 || true
  rm -f "$pid_file"
  echo "$name stopped"
}

status_one() {
  local name="$1"
  local pid_file="$2"
  local url="$3"
  local port="$4"
  local service="$5"

  adopt_managed_pid_if_needed "$service" "$pid_file"
  cleanup_pid_file_if_stale "$pid_file"
  local pid
  pid="$(read_pid "$pid_file")"
  if [[ -n "$pid" ]] && pid_is_running "$pid"; then
    echo "$name: running (PID $pid) - $url"
  elif port_has_listener "$port"; then
    local port_pid
    port_pid="$(find_port_pid "$port")"
    if [[ -n "$port_pid" ]]; then
      echo "$name: port $port is in use by an unmanaged process (PID $port_pid)"
    else
      echo "$name: port $port is in use by an unmanaged process"
    fi
  else
    echo "$name: stopped"
  fi
}

show_logs() {
  local service="$1"
  case "$service" in
    api)
      touch "$API_LOG"
      tail -f "$API_LOG"
      ;;
    worker)
      touch "$WORKER_LOG"
      tail -f "$WORKER_LOG"
      ;;
    web)
      touch "$WEB_LOG"
      tail -f "$WEB_LOG"
      ;;
    *)
      echo "logs requires one target: api, worker, or web" >&2
      exit 1
      ;;
  esac
}

run_for_target() {
  local operation="$1"
  case "$TARGET" in
    all)
      "$operation" api
      "$operation" worker
      "$operation" web
      ;;
    api|worker|web)
      "$operation" "$TARGET"
      ;;
    *)
      echo "Unknown target: $TARGET" >&2
      usage
      exit 1
      ;;
  esac
}

dispatch_start() {
  case "$1" in
    api) start_api ;;
    worker) start_worker ;;
    web) start_web ;;
  esac
}

dispatch_stop() {
  case "$1" in
    api) stop_one "API" "$API_PID_FILE" "api" ;;
    worker) stop_one "Task worker" "$WORKER_PID_FILE" "worker" ;;
    web) stop_one "Dashboard web" "$WEB_PID_FILE" "web" ;;
  esac
}

dispatch_status() {
  case "$1" in
    api) status_one "API" "$API_PID_FILE" "http://localhost:8000/api/v1/health" 8000 "api" ;;
    worker)
      adopt_managed_pid_if_needed "worker" "$WORKER_PID_FILE"
      cleanup_pid_file_if_stale "$WORKER_PID_FILE"
      local pid
      pid="$(read_pid "$WORKER_PID_FILE")"
      if [[ -n "$pid" ]] && pid_is_running "$pid"; then
        echo "Task worker: running (PID $pid) - background queue consumer"
      else
        echo "Task worker: stopped"
      fi
      ;;
    web) status_one "Dashboard web" "$WEB_PID_FILE" "http://localhost:3000" 3000 "web" ;;
  esac
}

case "$ACTION" in
  start)
    run_for_target dispatch_start
    ;;
  stop)
    if [[ "$TARGET" == "all" ]]; then
      dispatch_stop web
      dispatch_stop worker
      dispatch_stop api
    else
      run_for_target dispatch_stop
    fi
    ;;
  restart)
    if [[ "$TARGET" == "all" ]]; then
      dispatch_stop web
      dispatch_stop worker
      dispatch_stop api
      dispatch_start api
      dispatch_start worker
      dispatch_start web
    else
      run_for_target dispatch_stop
      run_for_target dispatch_start
    fi
    ;;
  status)
    dispatch_status api
    dispatch_status worker
    dispatch_status web
    ;;
  logs)
    show_logs "$TARGET"
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    echo "Unknown action: $ACTION" >&2
    usage
    exit 1
    ;;
esac
