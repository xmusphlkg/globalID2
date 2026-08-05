#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE_DIR="$ROOT_DIR/deploy/systemd"
SYSTEMD_DIR="/etc/systemd/system"
PROJECT_DIR="$ROOT_DIR"
RUN_USER="${SUDO_USER:-${USER:-$(id -un)}}"
RUN_GROUP="$(id -gn "$RUN_USER")"
GROUP_EXPLICIT=0
ENABLE_SERVICES=0
START_SERVICES=0
UNINSTALL_SERVICES=0
DRY_RUN=0

UNIT_NAMES=(
  globalid-stack.target
  globalid-docker.service
  globalid-dashboard-api.service
  globalid-dashboard-worker.service
  globalid-dashboard-web.service
  globalid-site.service
  globalid-notify-failure@.service
)

usage() {
  cat <<EOF
Usage:
  sudo ./scripts/install_systemd_services.sh [options]

Options:
  --user NAME         Linux user that should run the app services
  --group NAME        Linux group for the app services
  --project-dir PATH  Project root (default: current repository)
  --enable            Enable globalid-stack.target at boot
  --start             Start or restart the stack immediately
  --uninstall         Remove installed units and disable autostart
  --dry-run           Render unit files to a temp directory without installing
  -h, --help          Show this help

Examples:
  sudo ./scripts/install_systemd_services.sh --enable --start
  sudo ./scripts/install_systemd_services.sh --user likangguo --enable
  sudo ./scripts/install_systemd_services.sh --uninstall
EOF
}

render_unit() {
  local template_name="$1"
  local output_dir="$2"
  sed \
    -e "s#__PROJECT_DIR__#$PROJECT_DIR#g" \
    -e "s#__RUN_AS_USER__#$RUN_USER#g" \
    -e "s#__RUN_AS_GROUP__#$RUN_GROUP#g" \
    "$TEMPLATE_DIR/$template_name" > "$output_dir/$template_name"
}

ensure_root_for_install() {
  if [[ "$DRY_RUN" == "1" ]]; then
    return
  fi

  if [[ "$EUID" -ne 0 ]]; then
    echo "This script installs units into $SYSTEMD_DIR, so please run it with sudo." >&2
    exit 1
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --user)
      RUN_USER="$2"
      shift 2
      ;;
    --group)
      RUN_GROUP="$2"
      GROUP_EXPLICIT=1
      shift 2
      ;;
    --project-dir)
      PROJECT_DIR="$2"
      shift 2
      ;;
    --enable)
      ENABLE_SERVICES=1
      shift
      ;;
    --start)
      START_SERVICES=1
      shift
      ;;
    --uninstall)
      UNINSTALL_SERVICES=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"

if [[ "$GROUP_EXPLICIT" == "0" ]]; then
  RUN_GROUP="$(id -gn "$RUN_USER")"
fi

if [[ "$UNINSTALL_SERVICES" == "1" ]]; then
  ensure_root_for_install

  if [[ "$DRY_RUN" == "1" ]]; then
    echo "Dry run: would remove the following unit files from $SYSTEMD_DIR:"
    printf '  %s\n' "${UNIT_NAMES[@]}"
    exit 0
  fi

  systemctl stop globalid-stack.target >/dev/null 2>&1 || true
  systemctl disable globalid-stack.target >/dev/null 2>&1 || true

  for unit_name in "${UNIT_NAMES[@]}"; do
    rm -f "$SYSTEMD_DIR/$unit_name"
  done

  systemctl daemon-reload
  echo "Removed GlobalID systemd units."
  exit 0
fi

if ! id "$RUN_USER" >/dev/null 2>&1; then
  echo "User '$RUN_USER' does not exist." >&2
  exit 1
fi

if ! getent group "$RUN_GROUP" >/dev/null 2>&1; then
  echo "Group '$RUN_GROUP' does not exist." >&2
  exit 1
fi

if [[ ! -d "$TEMPLATE_DIR" ]]; then
  echo "Template directory not found: $TEMPLATE_DIR" >&2
  exit 1
fi

if [[ "$DRY_RUN" == "1" ]]; then
  OUTPUT_DIR="$(mktemp -d)"
else
  ensure_root_for_install
  OUTPUT_DIR="$SYSTEMD_DIR"
fi

if [[ "$DRY_RUN" == "1" ]]; then
  mkdir -p "$PROJECT_DIR/logs" "$PROJECT_DIR/astro-site/logs"
else
  install -d -o "$RUN_USER" -g "$RUN_GROUP" "$PROJECT_DIR/logs" "$PROJECT_DIR/astro-site/logs"
fi

for unit_name in "${UNIT_NAMES[@]}"; do
  render_unit "$unit_name" "$OUTPUT_DIR"
done

if [[ "$DRY_RUN" == "1" ]]; then
  echo "Rendered unit files to: $OUTPUT_DIR"
  printf '  %s\n' "${UNIT_NAMES[@]}"
  exit 0
fi

systemctl daemon-reload

if [[ "$ENABLE_SERVICES" == "1" ]]; then
  systemctl enable globalid-stack.target
fi

if [[ "$START_SERVICES" == "1" ]]; then
  systemctl restart globalid-stack.target
fi

echo "Installed GlobalID systemd units into $SYSTEMD_DIR"
echo "Run these commands to inspect the stack:"
echo "  systemctl status globalid-stack.target"
echo "  journalctl -u globalid-dashboard-api.service -f"
echo "  journalctl -u globalid-dashboard-web.service -f"
