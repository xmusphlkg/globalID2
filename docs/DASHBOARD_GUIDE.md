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

## Main Pages

- `/` overview dashboard
- `/diseases` disease analysis
- `/quality` data quality
- `/explorer` data explorer
- `/ai` AI tasks
- `/ai/interactions` AI interaction timeline
- `/reports` reports

## Notes

- Backend API should be running on `http://localhost:8000`.
- If API URL differs, set `NEXT_PUBLIC_API_URL` in `dashboard/.env.local`.

## Full Stack via Docker

Use the full dashboard stack file under `docker/`:

```bash
docker compose -f docker/dashboard-full-stack.yml up -d
```

This will start PostgreSQL, Redis, Qdrant, API, and Dashboard.
