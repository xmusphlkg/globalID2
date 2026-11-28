# Observability, Security Controls, and Quality Gates

Operator reference for Prometheus metrics, control-plane and infrastructure security controls, repository quality gates, and Astro build profiling.

---

## 1. Prometheus `/metrics` Endpoint & CLI

### Endpoint Contract

| Property | Value |
| --- | --- |
| URL | `GET /metrics` on the control-plane API (default `http://127.0.0.1:8000/metrics`) |
| Content-Type | `text/plain; version=0.0.4; charset=utf-8` |
| Schema & self-counting | Excluded from OpenAPI (`include_in_schema=False`) and excluded from `gids_http_requests_total` |
| Auth | Requires `DASHBOARD_API_KEY` (`X-Dashboard-API-Key` or `Authorization: Bearer <key>`) unless `METRICS_PUBLIC=true` |

### Local CLI Inspection

The read-only CLI prints raw Prometheus text format to stdout without Rich wrapping:

```bash
# Fast offline snapshot (process uptime, in-memory HTTP counters, DB pool if initialized; no DB/Redis I/O)
venv/bin/python -m src.cli.main metrics

# Force a live operational metrics scrape before rendering (fail-soft)
venv/bin/python -m src.cli.main metrics --include-operational
```

### Exposed Metrics

| Metric | Type | Labels | Description |
| --- | --- | --- | --- |
| `gids_process_uptime_seconds` | gauge | — | Control-plane process uptime in seconds. |
| `gids_http_requests_total` | counter | `method`, `route`, `status_class` | Total HTTP requests handled by the control-plane API. `route` uses the low-cardinality FastAPI route template (`unmatched` fallback; never raw resource IDs). `status_class` is `1xx`–`5xx` or `unknown`. |
| `gids_http_request_duration_seconds` | histogram | `method`, `route`, `status_class`, `le` (buckets) | HTTP request latency in seconds (`_bucket`, `_sum`, `_count`). Default buckets: `0.005`, `0.01`, `0.025`, `0.05`, `0.1`, `0.25`, `0.5`, `1`, `2.5`, `5`, `10`, `+Inf`. |
| `gids_db_pool_size` | gauge | — | Configured SQLAlchemy connection pool size (emitted once the async engine is initialized). |
| `gids_db_pool_checked_in` | gauge | — | Idle connections currently checked into the SQLAlchemy pool. |
| `gids_db_pool_checked_out` | gauge | — | Active connections currently checked out from the SQLAlchemy pool. |
| `gids_db_pool_overflow` | gauge | — | Current SQLAlchemy connection pool overflow count. |
| `gids_task_queue_tasks` | gauge | `status`, `task_type` | Count of active tasks (`pending`, `queued`, `running`, `retrying`) plus `failed` tasks from the last 24 hours. |
| `gids_publish_dirty_pending` | gauge | `scope_type` | Count of unpublished `publish_dirty` markers grouped by data line. |
| `gids_publish_dirty_oldest_age_seconds` | gauge | — | Age in seconds of the oldest unpublished `publish_dirty` marker (`0` when none pending). |
| `gids_runtime_components_alive` | gauge | `role` | Live runtime component heartbeats grouped by service role (e.g. `api`, `worker`, `ai_worker`, `scheduler`). |
| `gids_ai_models_enabled` | gauge | — | Enabled AI model routes backed by active providers. |
| `gids_operational_metrics_scrape_success` | gauge | — | Whether the last operational scrape succeeded (`1`) or failed/timed out (`0`). |
| `gids_operational_metrics_scrape_duration_seconds` | gauge | — | Wall-clock duration in seconds of the last operational scrape. |

### Environment Knobs

| Variable | Default | Purpose |
| --- | --- | --- |
| `METRICS_PUBLIC` | `false` | Set to `1`/`true`/`yes`/`on` to allow unauthenticated `GET /metrics` scrapes (use only on trusted loopback/private networks). |
| `METRICS_OPERATIONAL_ENABLED` | `true` | Enable or disable DB/runtime operational metrics collection during `/metrics` scrapes. |
| `METRICS_OPERATIONAL_CACHE_TTL_SECONDS` | `15.0` | In-process TTL cache duration (seconds, `>= 0`) with async lock coalescing across concurrent scrapes. |
| `METRICS_OPERATIONAL_TIMEOUT_SECONDS` | `1.5` | Hard timeout budget (seconds, `> 0`) for an uncached operational scrape. Failures are fail-soft and set `gids_operational_metrics_scrape_success 0` without failing `/metrics`. |

### Sample Prometheus Scrape Job

```yaml
scrape_configs:
  - job_name: globalid-control-plane
    scrape_interval: 15s
    scrape_timeout: 5s
    metrics_path: /metrics
    static_configs:
      - targets: ["127.0.0.1:8000"]
    # Omit when METRICS_PUBLIC=true on a loopback-only deployment:
    authorization:
      type: Bearer
      credentials_file: /etc/prometheus/secrets/globalid_dashboard_api_key
```

### High-Signal Alert Expressions

```yaml
groups:
  - name: globalid-control-plane
    rules:
      - alert: GlobalIDApiHigh5xxRate
        expr: |
          sum(rate(gids_http_requests_total{status_class="5xx"}[5m]))
          / clamp_min(sum(rate(gids_http_requests_total[5m])), 1e-6) > 0.05
        for: 5m

      - alert: GlobalIDApiHighLatencyP95
        expr: |
          histogram_quantile(
            0.95,
            sum by (le) (rate(gids_http_request_duration_seconds_bucket[5m]))
          ) > 2.0
        for: 10m

      - alert: GlobalIDOperationalMetricsScrapeFailing
        expr: gids_operational_metrics_scrape_success == 0
        for: 5m

      - alert: GlobalIDPublishDirtyBacklogStale
        expr: gids_publish_dirty_oldest_age_seconds > 1800
        for: 10m

      - alert: GlobalIDCoreWorkerDown
        expr: absent(gids_runtime_components_alive{role="worker"}) or gids_runtime_components_alive{role="worker"} < 1
        for: 3m

      - alert: GlobalIDNoEnabledAIModels
        expr: gids_ai_models_enabled == 0
        for: 5m

      - alert: GlobalIDDbPoolSaturated
        expr: |
          gids_db_pool_overflow > 0
          or (gids_db_pool_checked_out / clamp_min(gids_db_pool_size, 1)) > 0.85
        for: 5m
```

---

## 2. Control-Plane & Infrastructure Security Controls

### Control-Plane API Authentication (`dashboard/api/deps.py`, `dashboard/api/main.py`)

- **`DASHBOARD_API_KEY`**: Protects all `/api/v1/*` routes (except `OPTIONS`, `/health`, and `/api/v1/health`) and `/metrics` (unless `METRICS_PUBLIC=true`). Accepted via `X-Dashboard-API-Key` or `Authorization: Bearer <key>` using constant-time `hmac.compare_digest`.
- **Production fail-closed**: When `APP_ENV=production` (`config.is_production`) and `DASHBOARD_API_KEY` is empty, protected endpoints refuse traffic with HTTP `503` (`application/problem+json`, `code: "service_unavailable"`).
- **Break-glass override**: `DASHBOARD_ALLOW_UNAUTHENTICATED_PRODUCTION=true` disables the fail-closed check; use only inside isolated, non-public environments.

### CORS & Next.js Origin Controls

- **`DASHBOARD_CORS_ORIGINS`** (`dashboard/api/main.py`): Comma-separated FastAPI CORS allowlist (with `allow_credentials=true`). Defaults to `http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173`. Set explicit production origin(s) in `.env`.
- **`GLOBALID_DASHBOARD_ALLOWED_DEV_ORIGINS`** (`dashboard/next.config.ts`): Comma-separated allowlist for Next.js 16 `allowedDevOrigins` (defaults to `localhost,127.0.0.1`) so LAN dev access stays explicitly scoped.
- **Dashboard HTTP response headers** (`dashboard/next.config.ts`): Sets `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy`, `Cross-Origin-Opener-Policy: same-origin`, and disables `X-Powered-By`.

### Dashboard Panel Session & Login Rate Limiting (`dashboard/src/lib/panel-session.ts`, `dashboard/src/app/api/panel-session/route.ts`)

- **`DASHBOARD_PANEL_PASSWORD`**: Enables the dashboard login gate (verified via SHA-256 + `timingSafeEqual`).
- **`DASHBOARD_SESSION_SECRET`**: Dedicated HMAC-SHA256 signing key for the `gids_panel_session` cookie (7-day `maxAge`, `HttpOnly`, `SameSite=Lax`). Configure explicitly in production; if unset, derives a fallback key from `DASHBOARD_PANEL_PASSWORD`.
- **Automatic HTTPS `Secure` cookie**: Marks `gids_panel_session` as `Secure` whenever `X-Forwarded-Proto: https` or an `https://` request URL is observed.
- **Login rate limiting**: Tracks failed attempts per client IP (`X-Forwarded-For` → `X-Real-IP` → `CF-Connecting-IP` → `local`, bounded to 1024 tracked clients) in a 5-minute sliding window (`10` max attempts). Clients exceeding the limit receive HTTP `429` with a `Retry-After` header (seconds); a successful login clears the failure counter.

### Backing Service Bind Addresses (`docker-compose.yml`)

All Docker-exposed backing services bind to loopback by default so databases are never inadvertently exposed on `0.0.0.0`:

| Variable | Default | Container Ports |
| --- | --- | --- |
| `GLOBALID_POSTGRES_BIND_ADDRESS` | `127.0.0.1` | `5432` |
| `GLOBALID_REDIS_BIND_ADDRESS` | `127.0.0.1` | `6379` |
| `GLOBALID_QDRANT_BIND_ADDRESS` | `127.0.0.1` | `6333`, `6334` |

### Systemd Unit Hardening (`deploy/systemd/*.service`)

All non-Docker managed units (API, general worker, AI worker, model dispatchers, scheduler, dashboard web, static site, timers, and failure notifier) run as a dedicated non-root user/group with:

- `NoNewPrivileges=yes`
- `PrivateTmp=yes`
- Crash-loop guardrails (`StartLimitIntervalSec=300`, `StartLimitBurst=5`) and `OnFailure=globalid-notify-failure@%n.service` on core services.

---

## 3. Quality Gates & Build Profiling

### Unified Verification Targets (`Makefile`)

| Command | Scope |
| --- | --- |
| `make check` | Full read-only verification: `check-repository-boundaries` → `check-python` → `check-dashboard` → `check-site` → `check-workers`. No source edits, live network calls, or deployments. |
| `make check-repository-boundaries` | Runs `./scripts/check_repository_boundaries.sh` to enforce ignored generated-data paths and repository artifact size limits. |
| `make check-python` | `venv/bin/ruff check src dashboard/api scripts tests`, `python -m compileall`, and `python -m pytest -q`. |
| `make check-dashboard` | `cd dashboard && npm run lint && npm test`. |
| `make check-site` | `cd astro-site && npm run check && npm run test:all && npm run build:astro`. |
| `make check-workers` | Runs `check-subscriptions` (`CI=1 npm run verify && bash -n scripts/wrangler-env.sh`), `check-surveillance-data` (`npm run typecheck && npm test`), and `check-papers-json` (`npm run typecheck && npm test`). |

### Python Ruff Ratchet (`pyproject.toml`)

Ruff targets `py311` (`line-length = 120`) and enforces a curated, zero-regression rule set across `src/`, `dashboard/api/`, `scripts/`, and `tests/`:

- Active rules: `F821`, `F811` (ignored in `tests/unit/test_*.py`), `E722`, `B023`, `B033`, `ASYNC230`, `PLE2515`, `PLR0124`, `RUF024`, `DTZ003`, `F402`, `G201`, `FURB168`, `B010`, `RUF010`, `UP041`, `F841`.

### Dashboard Static Quality Checks (`dashboard/scripts/check-code-quality.mjs`)

`cd dashboard && npm run lint` runs `tsc --noEmit` followed by the zero-dependency static scanner across `dashboard/src/` and `dashboard/next.config.ts`:

- `no-unused-icon-libraries`: Bans `@heroicons/react` and `@remixicon/react` (use `lucide-react`).
- `no-console-log`: Bans `console.log` in non-test production code.
- `no-debugger`: Bans `debugger` statements.
- `no-hardcoded-lan-ip`: Bans hardcoded `192.168.*` LAN IPs in non-test code.
- `ts-ignore-requires-reason`: Requires a descriptive reason on `@ts-ignore` comments.

### Astro Unit Test Discovery & Build Profiling

- **Auto-discovered unit tests (`cd astro-site && npm run test:all`)**: Runs `astro-site/scripts/run-all-unit-tests.mjs`, which discovers every `*.test.{ts,mts,cts,js,mjs,cjs}` file under `scripts/` and `src/` (pass `-- --list` to inspect the discovered list) and executes them under Node's built-in test runner with `--experimental-strip-types`.
- **Parallel check worker cap (`GIDS_BUILD_CHECK_WORKERS`)**: Controls validation worker concurrency in `astro-site/scripts/build-pipeline.mjs`. Accepts integers **`1–6`** and defaults to `min(3, availableParallelism())` (`<= 3`). Each check worker is memory-bounded via `--max-old-space-size=1536`.
- **JSON build timing report (`GIDS_BUILD_TIMING_REPORT`)**: Set to a file path (relative to `astro-site/` or absolute) to emit structured stage timing JSON (`total_seconds`, `workers`, `status`, optional `error`, and per-stage `stages[]` records) even if a stage fails:

```bash
cd astro-site
GIDS_BUILD_CHECK_WORKERS=3 GIDS_BUILD_TIMING_REPORT=../reports/astro-build-timing.json npm run build:astro
```
