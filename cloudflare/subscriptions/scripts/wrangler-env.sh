#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUBSCRIPTIONS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$SUBSCRIPTIONS_DIR/../.." && pwd)"
WRANGLER_BIN="$REPO_ROOT/astro-site/node_modules/.bin/wrangler"
GENERATED_CONFIG="$SUBSCRIPTIONS_DIR/wrangler.generated.toml"

load_repo_env() {
  local env_path="$1"
  local python_bin="$REPO_ROOT/venv/bin/python3"
  local env_dump

  if [ ! -x "$python_bin" ]; then
    python_bin="$(command -v python3 || true)"
  fi
  if [ -z "$python_bin" ]; then
    echo "python3 is required to parse $env_path safely." >&2
    return 1
  fi

  env_dump="$(mktemp "${TMPDIR:-/tmp}/globalid-subscriptions-env.XXXXXX")"
  if ! "$python_bin" - "$env_path" > "$env_dump" <<'PY'
import re
import sys

from dotenv import dotenv_values

env_path = sys.argv[1]
for key, value in dotenv_values(env_path).items():
    if value is None or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) is None:
        continue
    sys.stdout.buffer.write(key.encode("utf-8") + b"\0")
    sys.stdout.buffer.write(value.encode("utf-8") + b"\0")
PY
  then
    rm -f "$env_dump"
    echo "Failed to parse $env_path with python-dotenv." >&2
    return 1
  fi

  local name value
  while IFS= read -r -d '' name && IFS= read -r -d '' value; do
    # Explicit process/service environment always wins over the repository file.
    if [ -z "${!name+x}" ]; then
      printf -v "$name" '%s' "$value"
      export "$name"
    fi
  done < "$env_dump"
  rm -f "$env_dump"
}

if [ -f "$REPO_ROOT/.env" ]; then
  load_repo_env "$REPO_ROOT/.env"
fi

if [ -z "${CLOUDFLARE_API_TOKEN:-}" ]; then
  echo "CLOUDFLARE_API_TOKEN is not set. Add it to $REPO_ROOT/.env or export it first." >&2
  exit 1
fi

toml_escape() {
  local value="${1:-}"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//$'\n'/ }"
  printf '%s' "$value"
}

bool_value() {
  case "${1:-}" in
    true|TRUE|1|yes|YES) printf 'true' ;;
    false|FALSE|0|no|NO|'') printf 'false' ;;
    *)
      echo "Invalid boolean value: $1" >&2
      exit 1
      ;;
  esac
}

require_env() {
  local name="$1"
  if [ -z "${!name:-}" ]; then
    echo "$name is not set. Add it to $REPO_ROOT/.env." >&2
    exit 1
  fi
}

write_config() {
  require_env SUBSCRIPTIONS__D1_DATABASE_NAME
  require_env SUBSCRIPTIONS__D1_DATABASE_ID

  local worker_name="${SUBSCRIPTIONS__WORKER_NAME:-globalid-subscriptions}"
  local compatibility_date="${SUBSCRIPTIONS__COMPATIBILITY_DATE:-2026-05-01}"
  local workers_dev
  workers_dev="$(bool_value "${SUBSCRIPTIONS__WORKERS_DEV:-true}")"
  local public_base_url="${SUBSCRIPTIONS__PUBLIC_BASE_URL:-http://localhost:8787}"
  local allowed_origins="${SUBSCRIPTIONS__ALLOWED_ORIGINS:-http://localhost:4321,https://globalinfectiousdisease.com}"
  local debug_return_tokens="${SUBSCRIPTIONS__DEBUG_RETURN_TOKENS:-false}"
  local d1_binding="${SUBSCRIPTIONS__D1_BINDING:-DB}"
  local smtp_host="${SUBSCRIPTIONS__SMTP_HOST:-${AUTOMATION__SMTP_HOST:-}}"
  local smtp_port="${SUBSCRIPTIONS__SMTP_PORT:-${AUTOMATION__SMTP_PORT:-587}}"
  local smtp_from_email="${SUBSCRIPTIONS__SMTP_FROM_EMAIL:-${AUTOMATION__SMTP_FROM_EMAIL:-}}"
  local smtp_from_name="${SUBSCRIPTIONS__SMTP_FROM_NAME:-GIDS Alerts}"
  local smtp_use_tls="${SUBSCRIPTIONS__SMTP_USE_TLS:-${AUTOMATION__SMTP_USE_TLS:-true}}"
  local pending_expiry_days="${SUBSCRIPTIONS__PENDING_EXPIRY_DAYS:-14}"
  local submission_rate_limit_per_hour="${SUBSCRIPTIONS__SUBMISSION_RATE_LIMIT_PER_HOUR:-30}"
  local confirmation_email_limit="${SUBSCRIPTIONS__CONFIRMATION_EMAIL_LIMIT_PER_10_MINUTES:-2}"
  local notification_batch_size="${SUBSCRIPTIONS__NOTIFICATION_BATCH_SIZE:-20}"
  local maintenance_cron="${SUBSCRIPTIONS__MAINTENANCE_CRON:-}"

  {
    printf 'name = "%s"\n' "$(toml_escape "$worker_name")"
    printf 'main = "src/index.ts"\n'
    printf 'compatibility_date = "%s"\n' "$(toml_escape "$compatibility_date")"
    printf 'workers_dev = %s\n' "$workers_dev"
    if [ -n "${CLOUDFLARE_ACCOUNT_ID:-}" ]; then
      printf 'account_id = "%s"\n' "$(toml_escape "$CLOUDFLARE_ACCOUNT_ID")"
    fi
    printf '\n[vars]\n'
    printf 'PUBLIC_BASE_URL = "%s"\n' "$(toml_escape "$public_base_url")"
    printf 'ALLOWED_ORIGINS = "%s"\n' "$(toml_escape "$allowed_origins")"
    printf 'DEBUG_RETURN_TOKENS = "%s"\n' "$(toml_escape "$debug_return_tokens")"
    printf 'PENDING_EXPIRY_DAYS = "%s"\n' "$(toml_escape "$pending_expiry_days")"
    printf 'SUBMISSION_RATE_LIMIT_PER_HOUR = "%s"\n' "$(toml_escape "$submission_rate_limit_per_hour")"
    printf 'CONFIRMATION_EMAIL_LIMIT_PER_10_MINUTES = "%s"\n' "$(toml_escape "$confirmation_email_limit")"
    printf 'NOTIFICATION_BATCH_SIZE = "%s"\n' "$(toml_escape "$notification_batch_size")"
    if [ -n "$smtp_host" ]; then
      printf 'SMTP_HOST = "%s"\n' "$(toml_escape "$smtp_host")"
      printf 'SMTP_PORT = "%s"\n' "$(toml_escape "$smtp_port")"
      printf 'SMTP_FROM_EMAIL = "%s"\n' "$(toml_escape "$smtp_from_email")"
      printf 'SMTP_FROM_NAME = "%s"\n' "$(toml_escape "$smtp_from_name")"
      printf 'SMTP_USE_TLS = "%s"\n' "$(toml_escape "$smtp_use_tls")"
    fi
    if [ -n "$maintenance_cron" ]; then
      printf '\n[triggers]\n'
      printf 'crons = ["%s"]\n' "$(toml_escape "$maintenance_cron")"
    fi
    printf '\n[[d1_databases]]\n'
    printf 'binding = "%s"\n' "$(toml_escape "$d1_binding")"
    printf 'database_name = "%s"\n' "$(toml_escape "$SUBSCRIPTIONS__D1_DATABASE_NAME")"
    printf 'database_id = "%s"\n' "$(toml_escape "$SUBSCRIPTIONS__D1_DATABASE_ID")"
    printf 'migrations_dir = "migrations"\n'
  } > "$GENERATED_CONFIG"
}

sync_secret() {
  local worker_secret_name="$1"
  local env_name="$2"
  local required="${3:-required}"
  local value="${!env_name:-}"

  if [ -z "$value" ]; then
    if [ "$required" = "required" ]; then
      echo "$env_name is not set. Add it to $REPO_ROOT/.env before syncing secrets." >&2
      exit 1
    fi
    echo "Skipping optional secret $worker_secret_name; $env_name is not set."
    return
  fi

  printf '%s' "$value" | "$WRANGLER_BIN" secret put "$worker_secret_name" -c "$GENERATED_CONFIG"
}

generate_options_sql() {
  local output_path="$1"
  local python_bin="$REPO_ROOT/venv/bin/python3"
  if [ ! -x "$python_bin" ]; then
    python_bin="python3"
  fi

  "$python_bin" - "$REPO_ROOT" > "$output_path" <<'PY'
import json
import sys
from pathlib import Path

repo = Path(sys.argv[1])
meta_path = repo / "astro-site" / "src" / "data" / "meta.json"
diseases_path = repo / "astro-site" / "src" / "data" / "diseases" / "index.json"

meta = json.loads(meta_path.read_text(encoding="utf-8"))
diseases = json.loads(diseases_path.read_text(encoding="utf-8"))
now = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"

preferred = [
    "influenza",
    "covid-19",
    "dengue",
    "measles",
    "malaria",
    "cholera",
    "hepatitis-a",
    "hepatitis-b",
    "rabies",
    "tuberculosis",
    "monkeypox",
    "hand-foot-mouth-disease",
]

def sql(value):
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"

print("""
UPDATE subscription_lists
SET name_zh = '报告更新', description_zh = '新的国家和疾病监测报告。', sort_order = 10
WHERE code = 'reports';
UPDATE subscription_lists
SET name_zh = '重点提醒', description_zh = '重要疫情、异常变化和高优先级监测提醒。', sort_order = 20
WHERE code = 'alerts';
UPDATE subscription_lists
SET name_zh = '每周摘要', description_zh = '每周汇总新的 GIDS 报告和重点变化。', sort_order = 30
WHERE code = 'weekly_digest';
""")

countries = sorted(meta.get("countries", []), key=lambda item: str(item.get("name", item.get("code", ""))))
for index, country in enumerate(countries, 1):
    code = str(country.get("code", "")).upper()
    name = str(country.get("name") or code)
    if not code:
        continue
    print(f"""
INSERT INTO subscription_filter_options (
  id, filter_type, filter_value, label_en, label_zh, sort_order, is_public, metadata_json, created_at, updated_at
) VALUES (
  'country:{code}', 'country', {sql(code)}, {sql(name)}, {sql(name)}, {index * 10}, 1, NULL, {now}, {now}
)
ON CONFLICT(filter_type, filter_value) DO UPDATE SET
  label_en = excluded.label_en,
  label_zh = excluded.label_zh,
  sort_order = excluded.sort_order,
  is_public = 1,
  updated_at = {now};
""")

preferred_rank = {slug: index for index, slug in enumerate(preferred, 1)}
for index, disease in enumerate(diseases, 1):
    slug = str(disease.get("slug", "")).lower()
    name_en = str(disease.get("name_en") or slug)
    name_zh = str(disease.get("name_zh") or name_en)
    if not slug:
        continue
    sort_order = preferred_rank.get(slug, 1000 + index)
    metadata = {
        "disease_id": disease.get("disease_id"),
        "category": disease.get("category"),
        "icd_10": disease.get("icd_10"),
        "icd_11": disease.get("icd_11"),
    }
    print(f"""
INSERT INTO subscription_filter_options (
  id, filter_type, filter_value, label_en, label_zh, description_en, sort_order, is_public, metadata_json, created_at, updated_at
) VALUES (
  'disease:{slug}', 'disease', {sql(slug)}, {sql(name_en)}, {sql(name_zh)}, {sql(disease.get("description"))},
  {sort_order}, 1, {sql(json.dumps(metadata, ensure_ascii=False, separators=(",", ":")))}, {now}, {now}
)
ON CONFLICT(filter_type, filter_value) DO UPDATE SET
  label_en = excluded.label_en,
  label_zh = excluded.label_zh,
  description_en = excluded.description_en,
  sort_order = excluded.sort_order,
  is_public = 1,
  metadata_json = excluded.metadata_json,
  updated_at = {now};
""")

PY
}

sync_secret_any() {
  local worker_secret_name="$1"
  local required="${2:-required}"
  shift 2

  local env_name
  local value=""
  for env_name in "$@"; do
    if [ -n "${!env_name:-}" ]; then
      value="${!env_name}"
      break
    fi
  done

  if [ -z "$value" ]; then
    if [ "$required" = "required" ]; then
      echo "$worker_secret_name source env is not set. Add one of: $*" >&2
      exit 1
    fi
    echo "Skipping optional secret $worker_secret_name; no source env is set."
    return
  fi

  printf '%s' "$value" | "$WRANGLER_BIN" secret put "$worker_secret_name" -c "$GENERATED_CONFIG"
}

cd "$SUBSCRIPTIONS_DIR"

case "${1:-}" in
  "")
    echo "Usage: $0 <whoami|deploy|dry-run|migrate-local|migrate-remote|sync-options-local|sync-options-remote|sync-secrets|wrangler args...>" >&2
    exit 1
    ;;
  whoami|login|logout)
    exec "$WRANGLER_BIN" "$@"
    ;;
  config-path)
    write_config
    printf '%s\n' "$GENERATED_CONFIG"
    ;;
  sync-secrets)
    write_config
    sync_secret TOKEN_SIGNING_SECRET SUBSCRIPTIONS__TOKEN_SIGNING_SECRET required
    sync_secret ADMIN_API_TOKEN SUBSCRIPTIONS__ADMIN_API_TOKEN required
    sync_secret TURNSTILE_SECRET_KEY SUBSCRIPTIONS__TURNSTILE_SECRET_KEY optional
    sync_secret_any SMTP_USERNAME optional SUBSCRIPTIONS__SMTP_USERNAME AUTOMATION__SMTP_USERNAME
    sync_secret_any SMTP_PASSWORD optional SUBSCRIPTIONS__SMTP_PASSWORD AUTOMATION__SMTP_PASSWORD
    ;;
  migrate-local)
    write_config
    exec "$WRANGLER_BIN" d1 migrations apply "$SUBSCRIPTIONS__D1_DATABASE_NAME" -c "$GENERATED_CONFIG" --local
    ;;
  migrate-remote)
    write_config
    exec "$WRANGLER_BIN" d1 migrations apply "$SUBSCRIPTIONS__D1_DATABASE_NAME" -c "$GENERATED_CONFIG" --remote
    ;;
  sync-options-local)
    write_config
    options_sql="$(mktemp)"
    generate_options_sql "$options_sql"
    "$WRANGLER_BIN" d1 execute "$SUBSCRIPTIONS__D1_DATABASE_NAME" -c "$GENERATED_CONFIG" --local --file "$options_sql"
    rm -f "$options_sql"
    ;;
  sync-options-remote)
    write_config
    options_sql="$(mktemp)"
    generate_options_sql "$options_sql"
    "$WRANGLER_BIN" d1 execute "$SUBSCRIPTIONS__D1_DATABASE_NAME" -c "$GENERATED_CONFIG" --remote --file "$options_sql"
    rm -f "$options_sql"
    ;;
  deploy)
    shift
    write_config
    exec "$WRANGLER_BIN" deploy "$@" -c "$GENERATED_CONFIG"
    ;;
  dry-run)
    shift
    write_config
    exec "$WRANGLER_BIN" deploy "$@" -c "$GENERATED_CONFIG" --dry-run --outdir /tmp/globalid-subscriptions-dry-run
    ;;
  *)
    write_config
    exec "$WRANGLER_BIN" "$@" -c "$GENERATED_CONFIG"
    ;;
esac
