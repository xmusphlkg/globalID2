#!/usr/bin/env bash

set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/service_common.sh"

ACTION="${1:-start}"
DOCKER_BIN="$(resolve_docker)"

cd "$ROOT_DIR"

case "$ACTION" in
  start)
    exec "$DOCKER_BIN" compose up -d
    ;;
  stop)
    exec "$DOCKER_BIN" compose stop
    ;;
  restart)
    "$DOCKER_BIN" compose stop
    exec "$DOCKER_BIN" compose up -d
    ;;
  status)
    exec "$DOCKER_BIN" compose ps
    ;;
  *)
    echo "Usage: $0 [start|stop|restart|status]" >&2
    exit 1
    ;;
esac
