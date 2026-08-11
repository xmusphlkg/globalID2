# Troubleshooting

## Runtime reports a missing service

Run `./scripts/dashboard.sh status` and inspect the corresponding log. A heartbeat expires after 45 seconds. If the process is running but absent, verify `REDIS_URL`, Redis reachability, and clock sanity. The task list continues polling when event streaming is unavailable.

## SSE disconnects or never updates

Confirm the BFF returns `text/event-stream` from `/api/v1/events/stream` and that the reverse proxy disables buffering. Check Redis and ensure `Last-Event-ID` is a valid Redis stream ID such as `1720000000000-0`. A non-stream ID starts from new events. Keep `Cache-Control: no-cache, no-transform` and `X-Accel-Buffering: no` intact.

## API returns a problem response

Copy `request_id` from the body/header and search structured API logs. Validation failures contain `field_errors`. `409` normally indicates an invalid state transition, such as retrying a running task. `401` indicates that the BFF and API do not share the same `DASHBOARD_API_KEY`.

## Readiness is degraded

`/health/live` only proves the process is alive. `/health/ready` also checks PostgreSQL. Run the migration preflight, validate the database URL, and confirm all required tables/columns exist. The API and scheduler never create or alter tables at startup.

## A schedule does not run

Check that the schedule is enabled, has a next run, and the scheduler heartbeat is present. Inspect `scheduled_job_states` for `last_status` and `last_error`, then open the schedule's task history. Redis loss affects heartbeats/events but not persisted next/last run projections.

## The UI shows stale or unavailable data

The browser must not use a backend URL directly. Inspect the same-origin `/api/v1/...` request, BFF `API_PROXY_TARGET`, mutation Origin, and request ID. Regenerate the client if API and frontend schemas differ:

```bash
cd dashboard && npm run openapi:check
```

## Build or test failure

```bash
cd dashboard
npm run lint
npm test
npm run build
cd ..
PYTHONPATH=. venv/bin/pytest -q tests/unit/test_control_plane_http_contract.py
```

Playwright tests require installed Chromium (`cd dashboard && npx playwright install chromium`) and a reachable API for data-dependent scenarios.
