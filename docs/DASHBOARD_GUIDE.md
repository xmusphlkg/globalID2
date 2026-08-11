# Dashboard Guide (Web)

## Current Dashboard

The legacy Streamlit dashboard has been removed.
The active dashboard is the Next.js app under `dashboard/`.

## Run Dashboard

```bash
cd dashboard
npm run dev
```

Open: http://localhost:3000

## Workspaces

- `/` Overview
- `/operations/*` Ingestion & Tasks
- `/data/*` Data Governance
- `/production/*` AI & Reports
- `/settings/*` Settings

## Notes

- Backend API should be running on `http://localhost:8000`.
- The browser uses only same-origin `/api/v1`; set server-only `API_PROXY_TARGET` if the API target differs.
- Operational documentation is indexed in `docs/control-center/README.md`.

## Full Stack via Docker

Use the full dashboard stack file under `docker/`:

```bash
docker compose -f docker/dashboard-full-stack.yml up -d
```

This starts PostgreSQL, Redis, Qdrant, API, scheduler, worker, and Dashboard.
