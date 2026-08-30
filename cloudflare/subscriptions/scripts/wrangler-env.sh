#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUBSCRIPTIONS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$SUBSCRIPTIONS_DIR/../.." && pwd)"
WRANGLER_BIN="$SUBSCRIPTIONS_DIR/node_modules/.bin/wrangler"
GENERATED_CONFIG="$SUBSCRIPTIONS_DIR/wrangler.generated.jsonc"
DEFAULT_COMPATIBILITY_DATE="2026-08-17"

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

try:
    from dotenv import dotenv_values
except ModuleNotFoundError:
    def dotenv_values(path):
        """Minimal non-evaluating fallback for environments without python-dotenv."""
        values = {}
        for raw_line in open(path, encoding="utf-8"):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[:1] == value[-1:] and value[:1] in {"'", '"'}:
                value = value[1:-1]
            values[key] = value
        return values

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

require_env() {
  local name="$1"
  if [ -z "${!name:-}" ]; then
    echo "$name is not set. Add it to $REPO_ROOT/.env." >&2
    exit 1
  fi
}

write_config() {
  local target_environment="${1:-local}"
  local python_bin="$REPO_ROOT/venv/bin/python3"
  if [ ! -x "$python_bin" ]; then
    python_bin="$(command -v python3 || true)"
  fi
  if [ -z "$python_bin" ]; then
    echo "python3 is required to generate Wrangler configuration safely." >&2
    return 1
  fi

  SUBSCRIPTIONS_CONFIG_TARGET="$target_environment" \
  SUBSCRIPTIONS_CONFIG_PATH="$GENERATED_CONFIG" \
  SUBSCRIPTIONS_DEFAULT_COMPATIBILITY_DATE="$DEFAULT_COMPATIBILITY_DATE" \
    "$python_bin" - <<'PY'
import datetime as dt
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

target = os.environ["SUBSCRIPTIONS_CONFIG_TARGET"].strip().lower()
if target not in {"local", "staging", "production"}:
    raise SystemExit("Environment must be one of: local, staging, production.")

def value(name, default=""):
    return os.environ.get(name, default).strip()

def boolean(name, default):
    raw = value(name, "true" if default else "false").lower()
    if raw in {"true", "1", "yes"}:
        return True
    if raw in {"false", "0", "no", ""}:
        return False
    raise SystemExit(f"{name} must be a boolean value.")

def integer(name, default, minimum=1, maximum=1_000_000):
    raw = value(name, str(default))
    try:
        parsed = int(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} must be an integer.") from exc
    if not minimum <= parsed <= maximum:
        raise SystemExit(f"{name} must be between {minimum} and {maximum}.")
    return str(parsed)

def sample_rate(name, default):
    raw = value(name, str(default))
    try:
        parsed = float(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} must be a number from 0 to 1.") from exc
    if not 0 <= parsed <= 1:
        raise SystemExit(f"{name} must be a number from 0 to 1.")
    return parsed

def https_origin(name, raw, *, allow_local=False):
    parsed = urlsplit(raw)
    local_http = allow_local and parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}
    if (parsed.scheme != "https" and not local_http) or not parsed.hostname or parsed.username or parsed.password:
        raise SystemExit(f"{name} must be an HTTPS origin without credentials.")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise SystemExit(f"{name} must not contain a path, query, or fragment.")
    return raw.rstrip("/")

worker_name = value("SUBSCRIPTIONS__WORKER_NAME", "globalid-subscriptions")
if re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", worker_name) is None:
    raise SystemExit("SUBSCRIPTIONS__WORKER_NAME must be a workers.dev-safe lowercase name.")

compatibility_date = value("SUBSCRIPTIONS__COMPATIBILITY_DATE", os.environ["SUBSCRIPTIONS_DEFAULT_COMPATIBILITY_DATE"])
try:
    parsed_date = dt.date.fromisoformat(compatibility_date)
except ValueError as exc:
    raise SystemExit("SUBSCRIPTIONS__COMPATIBILITY_DATE must use YYYY-MM-DD.") from exc
today = dt.date.today()
if parsed_date > today:
    raise SystemExit("SUBSCRIPTIONS__COMPATIBILITY_DATE cannot be in the future.")
if (today - parsed_date).days > 180:
    raise SystemExit("SUBSCRIPTIONS__COMPATIBILITY_DATE is more than 180 days old.")

binding = value("SUBSCRIPTIONS__D1_BINDING", "DB")
if binding != "DB":
    raise SystemExit("SUBSCRIPTIONS__D1_BINDING must be DB because the Worker uses env.DB.")

def runtime_vars(environment):
    is_local = environment == "local"
    public_url = (
        value("SUBSCRIPTIONS_LOCAL__PUBLIC_BASE_URL", "http://localhost:8787")
        if is_local else value("SUBSCRIPTIONS__PUBLIC_BASE_URL")
    )
    if not public_url:
        raise SystemExit("SUBSCRIPTIONS__PUBLIC_BASE_URL is required for remote environments.")
    public_url = https_origin("SUBSCRIPTIONS__PUBLIC_BASE_URL", public_url, allow_local=is_local)
    allowed = (
        value("SUBSCRIPTIONS_LOCAL__ALLOWED_ORIGINS", "http://localhost:4321")
        if is_local else value("SUBSCRIPTIONS__ALLOWED_ORIGINS", "https://globalinfectiousdisease.com")
    )
    for origin in filter(None, (part.strip() for part in allowed.split(","))):
        https_origin("SUBSCRIPTIONS__ALLOWED_ORIGINS", origin, allow_local=is_local)
    situation_origins = value("SUBSCRIPTIONS__SITUATION_PUBLIC_ORIGINS", "https://globalinfectiousdisease.com")
    for origin in filter(None, (part.strip() for part in situation_origins.split(","))):
        https_origin("SUBSCRIPTIONS__SITUATION_PUBLIC_ORIGINS", origin)
    debug_tokens = boolean(
        "SUBSCRIPTIONS_LOCAL__DEBUG_RETURN_TOKENS" if is_local else "SUBSCRIPTIONS__DEBUG_RETURN_TOKENS",
        False,
    )
    if not is_local and debug_tokens:
        raise SystemExit("SUBSCRIPTIONS__DEBUG_RETURN_TOKENS must be false outside local development.")
    result = {
        "ENVIRONMENT": environment,
        "PUBLIC_BASE_URL": public_url,
        "ALLOWED_ORIGINS": allowed,
        "DEBUG_RETURN_TOKENS": "true" if debug_tokens else "false",
        "PENDING_EXPIRY_DAYS": integer("SUBSCRIPTIONS__PENDING_EXPIRY_DAYS", 14, 1, 365),
        "SUBMISSION_RATE_LIMIT_PER_HOUR": integer("SUBSCRIPTIONS__SUBMISSION_RATE_LIMIT_PER_HOUR", 30, 1, 10_000),
        "CONFIRMATION_EMAIL_LIMIT_PER_10_MINUTES": integer("SUBSCRIPTIONS__CONFIRMATION_EMAIL_LIMIT_PER_10_MINUTES", 2, 1, 100),
        "NOTIFICATION_BATCH_SIZE": integer("SUBSCRIPTIONS__NOTIFICATION_BATCH_SIZE", 20, 1, 500),
        "SITUATION_ALERT_BATCH_SIZE": integer("SUBSCRIPTIONS__SITUATION_ALERT_BATCH_SIZE", 20, 1, 500),
        "SITUATION_ALERT_MAX_ATTEMPTS": integer("SUBSCRIPTIONS__SITUATION_ALERT_MAX_ATTEMPTS", 5, 1, 20),
        "SITUATION_ALERT_RETENTION_DAYS": integer("SUBSCRIPTIONS__SITUATION_ALERT_RETENTION_DAYS", 180, 7, 3650),
        "SITUATION_ALERT_AUTOMATED_POLICY_ENABLED": "true" if boolean("SUBSCRIPTIONS__SITUATION_ALERT_AUTOMATED_POLICY_ENABLED", False) else "false",
        "SITUATION_PUBLIC_ORIGINS": situation_origins,
    }
    smtp_host = "" if is_local else value("SUBSCRIPTIONS__SMTP_HOST", value("AUTOMATION__SMTP_HOST"))
    smtp_from = "" if is_local else value("SUBSCRIPTIONS__SMTP_FROM_EMAIL", value("AUTOMATION__SMTP_FROM_EMAIL"))
    if smtp_host or smtp_from:
        if not smtp_host or not smtp_from or "@" not in smtp_from:
            raise SystemExit("SMTP host and a valid sender address must be configured together.")
        smtp_tls = boolean("SUBSCRIPTIONS__SMTP_USE_TLS", boolean("AUTOMATION__SMTP_USE_TLS", True))
        if not is_local and not smtp_tls:
            raise SystemExit("SMTP TLS cannot be disabled in a remote environment.")
        result.update({
            "SMTP_HOST": smtp_host,
            "SMTP_PORT": integer("SUBSCRIPTIONS__SMTP_PORT", int(value("AUTOMATION__SMTP_PORT", "587")), 1, 65535),
            "SMTP_FROM_EMAIL": smtp_from,
            "SMTP_FROM_NAME": value("SUBSCRIPTIONS__SMTP_FROM_NAME", "GIDS Alerts"),
            "SMTP_USE_TLS": "true" if smtp_tls else "false",
        })
    return result

observability = {
    "enabled": True,
    "logs": {
        "enabled": True,
        "head_sampling_rate": sample_rate("SUBSCRIPTIONS__LOG_SAMPLING_RATE", 1),
        "invocation_logs": True,
    },
    "traces": {
        "enabled": True,
        "head_sampling_rate": sample_rate("SUBSCRIPTIONS__TRACE_SAMPLING_RATE", 0.05),
    },
}

config = {
    "$schema": "./node_modules/wrangler/config-schema.json",
    "name": f"{worker_name}-local",
    "main": "src/index.ts",
    "compatibility_date": compatibility_date,
    "compatibility_flags": ["nodejs_compat"],
    "workers_dev": True,
    "vars": runtime_vars("local"),
    "observability": observability,
    "d1_databases": [{
        "binding": "DB",
        "database_name": f"{worker_name}-local",
        "migrations_dir": "migrations",
    }],
}

account_id = value("CLOUDFLARE_ACCOUNT_ID")
if account_id:
    if re.fullmatch(r"[0-9a-fA-F]{32}", account_id) is None:
        raise SystemExit("CLOUDFLARE_ACCOUNT_ID must be a 32-character hexadecimal account ID.")
    config["account_id"] = account_id

if target != "local":
    database_name = value("SUBSCRIPTIONS__D1_DATABASE_NAME")
    database_id = value("SUBSCRIPTIONS__D1_DATABASE_ID")
    if not database_name or not database_id:
        raise SystemExit("SUBSCRIPTIONS__D1_DATABASE_NAME and SUBSCRIPTIONS__D1_DATABASE_ID are required for remote environments.")
    if re.fullmatch(r"[0-9a-fA-F-]{36}", database_id) is None:
        raise SystemExit("SUBSCRIPTIONS__D1_DATABASE_ID must be a UUID.")
    if target == "staging" and "stag" not in database_name.lower():
        raise SystemExit("The staging D1 database name must contain 'stag' to prevent production reuse.")
    workers_dev = boolean("SUBSCRIPTIONS__WORKERS_DEV", target != "production")
    if target == "production" and workers_dev:
        raise SystemExit("SUBSCRIPTIONS__WORKERS_DEV must be false in production.")
    target_config = {
        "name": worker_name if target == "production" else f"{worker_name}-{target}",
        "workers_dev": workers_dev,
        "vars": runtime_vars(target),
        "observability": observability,
        "d1_databases": [{
            "binding": "DB",
            "database_name": database_name,
            "database_id": database_id,
            "migrations_dir": "migrations",
        }],
    }
    cron = value("SUBSCRIPTIONS__MAINTENANCE_CRON", "*/5 * * * *") or "*/5 * * * *"
    if cron.lower() != "disabled":
        target_config["triggers"] = {"crons": [cron]}
    queue_name = value("SUBSCRIPTIONS__SITUATION_ALERT_QUEUE_NAME")
    dead_letter = value("SUBSCRIPTIONS__SITUATION_ALERT_DEAD_LETTER_QUEUE")
    if dead_letter and not queue_name:
        raise SystemExit("A dead-letter Queue requires SUBSCRIPTIONS__SITUATION_ALERT_QUEUE_NAME.")
    if queue_name:
        consumer = {
            "queue": queue_name,
            "max_batch_size": 10,
            "max_batch_timeout": 5,
            "max_retries": 5,
            "retry_delay": 60,
        }
        if dead_letter:
            consumer["dead_letter_queue"] = dead_letter
        target_config["queues"] = {
            "producers": [{"binding": "SITUATION_ALERT_QUEUE", "queue": queue_name}],
            "consumers": [consumer],
        }
    config["env"] = {target: target_config}

path = Path(os.environ["SUBSCRIPTIONS_CONFIG_PATH"])
path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
path.chmod(0o600)
PY
}

require_remote_target() {
  local target="${1:-}"
  if [ "$target" != "staging" ] && [ "$target" != "production" ]; then
    echo "Specify the remote environment explicitly: staging or production." >&2
    exit 1
  fi
}

require_remote_auth() {
  require_env CLOUDFLARE_API_TOKEN
  require_env CLOUDFLARE_ACCOUNT_ID
}

require_gate() {
  local gate_name="$1"
  local target="$2"
  if [ "${!gate_name:-}" != "$target" ]; then
    echo "$gate_name must equal '$target' for this mutating command." >&2
    exit 1
  fi
}

prepare_run_config() {
  local target="$1"
  GENERATED_CONFIG="$(mktemp "$SUBSCRIPTIONS_DIR/wrangler.run.${target}.XXXXXX.jsonc")"
  write_config "$target"
}

sync_secret() {
  local worker_secret_name="$1"
  local env_name="$2"
  local required="${3:-required}"
  local target="$4"
  local value="${!env_name:-}"

  if [ -z "$value" ]; then
    if [ "$required" = "required" ]; then
      echo "$env_name is not set. Add it to $REPO_ROOT/.env before syncing secrets." >&2
      exit 1
    fi
    echo "Skipping optional secret $worker_secret_name; $env_name is not set."
    return
  fi

  printf '%s' "$value" | "$WRANGLER_BIN" secret put "$worker_secret_name" \
    -c "$GENERATED_CONFIG" --env "$target"
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
  local target="$3"
  shift 3

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

  printf '%s' "$value" | "$WRANGLER_BIN" secret put "$worker_secret_name" \
    -c "$GENERATED_CONFIG" --env "$target"
}

cd "$SUBSCRIPTIONS_DIR"

case "${1:-}" in
  "")
    echo "Usage: $0 <whoami|config-path [env]|dry-run env|startup env|migrate-local [persist-dir]|migration-plan env|backup-remote env [output]|migrate-remote env|sync-options-local|sync-options-remote env|sync-secrets env|deploy env>" >&2
    exit 1
    ;;
  whoami|login|logout)
    exec "$WRANGLER_BIN" "$@"
    ;;
  config-path)
    write_config "${2:-local}"
    printf '%s\n' "$GENERATED_CONFIG"
    ;;
  sync-secrets)
    target="${2:-}"
    require_remote_target "$target"
    require_remote_auth
    require_gate SUBSCRIPTIONS__ALLOW_SECRET_SYNC "$target"
    prepare_run_config "$target"
    sync_secret TOKEN_SIGNING_SECRET SUBSCRIPTIONS__TOKEN_SIGNING_SECRET required "$target"
    sync_secret ADMIN_API_TOKEN SUBSCRIPTIONS__ADMIN_API_TOKEN required "$target"
    sync_secret SITUATION_ALERT_INGEST_TOKEN SUBSCRIPTIONS__SITUATION_ALERT_INGEST_TOKEN required "$target"
    sync_secret EMAIL_DELIVERY_INGEST_TOKEN SUBSCRIPTIONS__EMAIL_DELIVERY_INGEST_TOKEN required "$target"
    sync_secret TURNSTILE_SECRET_KEY SUBSCRIPTIONS__TURNSTILE_SECRET_KEY optional "$target"
    smtp_requirement="optional"
    if [ -n "${SUBSCRIPTIONS__SMTP_HOST:-${AUTOMATION__SMTP_HOST:-}}" ]; then
      smtp_requirement="required"
    fi
    sync_secret_any SMTP_USERNAME "$smtp_requirement" "$target" SUBSCRIPTIONS__SMTP_USERNAME AUTOMATION__SMTP_USERNAME
    sync_secret_any SMTP_PASSWORD "$smtp_requirement" "$target" SUBSCRIPTIONS__SMTP_PASSWORD AUTOMATION__SMTP_PASSWORD
    ;;
  migrate-local)
    prepare_run_config local
    if [ -n "${2:-}" ]; then
      exec "$WRANGLER_BIN" d1 migrations apply DB -c "$GENERATED_CONFIG" --local --persist-to "$2"
    fi
    exec "$WRANGLER_BIN" d1 migrations apply DB -c "$GENERATED_CONFIG" --local
    ;;
  migrate-remote)
    target="${2:-}"
    require_remote_target "$target"
    require_remote_auth
    require_gate SUBSCRIPTIONS__ALLOW_REMOTE_MIGRATION "$target"
    prepare_run_config "$target"
    backup_dir="$SUBSCRIPTIONS_DIR/backups"
    mkdir -p "$backup_dir"
    backup_run_dir="$(mktemp -d "$backup_dir/${target}-pre-migration.XXXXXX")"
    backup_path="$backup_run_dir/backup.sql"
    "$WRANGLER_BIN" d1 export "$SUBSCRIPTIONS__D1_DATABASE_NAME" -c "$GENERATED_CONFIG" \
      --env "$target" --remote --output "$backup_path"
    "$WRANGLER_BIN" d1 migrations apply "$SUBSCRIPTIONS__D1_DATABASE_NAME" \
      -c "$GENERATED_CONFIG" --env "$target" --remote
    printf 'Pre-migration export: %s\n' "$backup_path"
    ;;
  migration-plan)
    target="${2:-}"
    require_remote_target "$target"
    require_remote_auth
    prepare_run_config "$target"
    exec "$WRANGLER_BIN" d1 migrations list "$SUBSCRIPTIONS__D1_DATABASE_NAME" \
      -c "$GENERATED_CONFIG" --env "$target" --remote
    ;;
  backup-remote)
    target="${2:-}"
    require_remote_target "$target"
    require_remote_auth
    prepare_run_config "$target"
    if [ -n "${3:-}" ]; then
      backup_path="$3"
    else
      backup_dir="$SUBSCRIPTIONS_DIR/backups"
      mkdir -p "$backup_dir"
      backup_run_dir="$(mktemp -d "$backup_dir/${target}-manual.XXXXXX")"
      backup_path="$backup_run_dir/backup.sql"
    fi
    mkdir -p "$(dirname "$backup_path")"
    exec "$WRANGLER_BIN" d1 export "$SUBSCRIPTIONS__D1_DATABASE_NAME" \
      -c "$GENERATED_CONFIG" --env "$target" --remote --output "$backup_path"
    ;;
  sync-options-local)
    prepare_run_config local
    options_sql="$(mktemp)"
    generate_options_sql "$options_sql"
    "$WRANGLER_BIN" d1 execute DB -c "$GENERATED_CONFIG" --local --file "$options_sql"
    rm -f "$options_sql"
    ;;
  sync-options-remote)
    target="${2:-}"
    require_remote_target "$target"
    require_remote_auth
    require_gate SUBSCRIPTIONS__ALLOW_REMOTE_OPTION_SYNC "$target"
    prepare_run_config "$target"
    options_sql="$(mktemp)"
    generate_options_sql "$options_sql"
    "$WRANGLER_BIN" d1 execute "$SUBSCRIPTIONS__D1_DATABASE_NAME" -c "$GENERATED_CONFIG" \
      --env "$target" --remote --file "$options_sql"
    rm -f "$options_sql"
    ;;
  deploy)
    target="${2:-}"
    require_remote_target "$target"
    require_remote_auth
    require_gate SUBSCRIPTIONS__ALLOW_DEPLOY "$target"
    shift 2
    prepare_run_config "$target"
    exec "$WRANGLER_BIN" deploy "$@" -c "$GENERATED_CONFIG" --env "$target" --strict
    ;;
  dry-run)
    target="${2:-}"
    require_remote_target "$target"
    shift 2
    prepare_run_config "$target"
    mkdir -p "$SUBSCRIPTIONS_DIR/.dry-run"
    run_dir="$(mktemp -d "$SUBSCRIPTIONS_DIR/.dry-run/${target}.XXXXXX")"
    printf 'Dry-run artifact directory: %s\n' "$run_dir"
    exec "$WRANGLER_BIN" deploy "$@" -c "$GENERATED_CONFIG" --env "$target" \
      --dry-run --outdir "$run_dir/bundle"
    ;;
  startup)
    target="${2:-}"
    require_remote_target "$target"
    shift 2
    prepare_run_config "$target"
    mkdir -p "$SUBSCRIPTIONS_DIR/.dry-run"
    run_dir="$(mktemp -d "$SUBSCRIPTIONS_DIR/.dry-run/${target}-startup.XXXXXX")"
    printf 'Startup profile directory: %s\n' "$run_dir"
    exec "$WRANGLER_BIN" check startup "$@" -c "$GENERATED_CONFIG" --env "$target" \
      --args="--config $GENERATED_CONFIG --env $target" \
      --outfile "$run_dir/worker-startup.cpuprofile"
    ;;
  *)
    echo "Unsupported command '$1'. Use an explicit guarded command shown by running the script without arguments." >&2
    exit 1
    ;;
esac
