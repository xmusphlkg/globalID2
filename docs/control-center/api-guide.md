# API Guide

FastAPI OpenAPI is the only contract source. Swagger is available at `/docs`, ReDoc at `/redoc`, and the schema at `/openapi.json`. The checked-in browser contract is `dashboard/openapi.json`; TypeScript declarations are generated into `dashboard/src/generated/api.d.ts`.

## Protocol

- All control-plane resources are under `/api/v1`.
- Success: `{ "data": ..., "meta": { "request_id": "..." } }`.
- Lists additionally expose `meta.pagination` when the route supplies a total.
- Errors use `application/problem+json` with `type`, `title`, `status`, `detail`, `code`, `request_id`, and optional `field_errors`.
- Send `X-Request-ID` to preserve a caller correlation ID. Otherwise the API creates one.
- Asynchronous mutations return `202` and a task reference. Configuration creation returns `201`.
- Public probes are `/health/live` and `/health/ready`.

## Resource map

| Area | Resources |
| --- | --- |
| Overview | `/overview`, `/action-items`, `/runtime/services`, `/events/stream` |
| Operations | `/countries`, `/sources/*`, `/tasks`, `/tasks/{uuid}`, `/tasks/{uuid}/events`, `/tasks/{uuid}/cancel`, `/tasks/{uuid}/retry`, `/schedules`, `/schedules/{id}`, `/schedules/{id}/runs` |
| Governance | `/analytics/*`, `/diseases/*`, `/quality/*`, `/catalog/*`, `/knowledge/*`, `/mappings/*` |
| Production | `/ai/*`, `/reports/*`, `/releases/*`, `/subscriptions/*`, `/notification-campaigns/*` |
| Settings | `/settings/*`, `/settings/{section}/test` |

Stable external identifiers are country codes, disease codes, task/report UUIDs, and stable string IDs. Unified schedule IDs use `ingestion:<job-id>` or `release:<job-id>`.

## Examples

Set the server-only key for direct API administration:

```bash
export GIDS_ADMIN_KEY='replace-with-configured-key'
```

Start a country ingestion run:

```bash
curl -X POST http://localhost:8000/api/v1/sources/DE/runs \
  -H "X-Dashboard-API-Key: $GIDS_ADMIN_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"source":"all","priority":"normal","process":true,"save_raw":true}'
```

Cancel or retry a task:

```bash
curl -X POST -H "X-Dashboard-API-Key: $GIDS_ADMIN_KEY" http://localhost:8000/api/v1/tasks/TASK_UUID/cancel
curl -X POST -H "X-Dashboard-API-Key: $GIDS_ADMIN_KEY" http://localhost:8000/api/v1/tasks/TASK_UUID/retry
```

Trigger a schedule or release:

```bash
curl -X POST -H "X-Dashboard-API-Key: $GIDS_ADMIN_KEY" 'http://localhost:8000/api/v1/schedules/ingestion:de-rki/runs'
curl -X POST -H "X-Dashboard-API-Key: $GIDS_ADMIN_KEY" http://localhost:8000/api/v1/releases/site-release/runs
```

Create and test configuration using the schemas shown by Swagger:

```bash
curl -X POST -H "X-Dashboard-API-Key: $GIDS_ADMIN_KEY" -H 'Content-Type: application/json' http://localhost:8000/api/v1/ai/models/providers -d @provider.json
curl -X POST -H "X-Dashboard-API-Key: $GIDS_ADMIN_KEY" http://localhost:8000/api/v1/settings/smtp/test
```

Create and send a notification campaign:

```bash
curl -X POST -H "X-Dashboard-API-Key: $GIDS_ADMIN_KEY" -H 'Content-Type: application/json' http://localhost:8000/api/v1/notification-campaigns -d @campaign.json
curl -X POST -H "X-Dashboard-API-Key: $GIDS_ADMIN_KEY" 'http://localhost:8000/api/v1/notification-campaigns/CAMPAIGN_ID/send?batch_size=20'
```

## SSE

Connect to `GET /api/v1/events/stream`. Events include `task.status`, `task.progress`, `schedule.triggered`, `runtime.started`, and `runtime.stopped`. The stream emits a keepalive every 15 seconds. Browsers automatically reconnect and send `Last-Event-ID`; other clients should persist the last `id:` field and send it as the `Last-Event-ID` header.

## Contract generation

```bash
cd dashboard
npm run openapi:generate
npm run openapi:check
```

Feature modules should import `components`/`paths` from `src/generated/api.d.ts` and call `src/generated/client.ts`. Do not create parallel handwritten response DTOs.
