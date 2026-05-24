# Dashboard Development Guide

This directory contains the GIDS dashboard subsystem, which has two parts:

- Next.js frontend, listening on `http://localhost:3000` by default
- FastAPI backend, listening on `http://localhost:8000` by default

The frontend proxies `/api/v1/*` to `API_PROXY_TARGET`, which defaults to `http://localhost:8000`. The server-side proxy can inject `DASHBOARD_API_KEY` for protected API deployments. If you start only the frontend and not the backend, the country selector in the UI will show `Countries unavailable`.

## Directory Layout

```text
dashboard/
├── api/                # FastAPI backend entrypoint and routers
├── src/                # Next.js frontend source
├── .env.local          # Local frontend proxy and WebSocket config
├── next.config.ts      # Next.js runtime config
└── package.json        # Frontend dependencies and npm scripts
```

## Prerequisites

- A Python virtual environment with the root `requirements.txt` installed
- `dashboard/node_modules` installed
- Database and other backend dependencies available

Recommended setup from the repository root:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cd dashboard
npm install
cd ..
```

## One-Command Startup

The recommended way is to use the root-level control script:

```bash
./scripts/dashboard.sh start
```

By default this starts both:

- API: `http://localhost:8000`
- Dashboard: `http://localhost:3000`

Common commands:

```bash
./scripts/dashboard.sh start         # Start API + worker + frontend
./scripts/dashboard.sh start api     # Start only the API
./scripts/dashboard.sh start worker  # Start only the task worker
./scripts/dashboard.sh start web     # Start only the frontend
./scripts/dashboard.sh stop          # Stop API + worker + frontend
./scripts/dashboard.sh restart       # Restart API + worker + frontend
./scripts/dashboard.sh status        # Show current status
./scripts/dashboard.sh logs api      # Tail API logs
./scripts/dashboard.sh logs worker   # Tail task worker logs
./scripts/dashboard.sh logs web      # Tail frontend logs
DASHBOARD_API_RELOAD=1 ./scripts/dashboard.sh start api  # API hot reload for development
```

Log files are written to:

- `logs/dashboard-api.log`
- `logs/dashboard-worker.log`
- `logs/dashboard-web.log`

The dashboard now uses a separate worker process for task execution. API endpoints only queue tasks (status becomes `queued`), and the worker executes them independently. This prevents long-running tasks from being interrupted by API reloads.

## Manual Startup

If you want to debug each service separately:

### 1. Start the API

Run this from the repository root:

```bash
source venv/bin/activate
uvicorn dashboard.api.main:app --host 0.0.0.0 --port 8000
```

Use hot reload only when actively editing backend code:

```bash
uvicorn dashboard.api.main:app --reload --host 0.0.0.0 --port 8000
```

Health checks:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/api/v1/health
```

### 2. Start the Frontend

```bash
cd dashboard
npm run dev -- --port 3000
```

Open `http://localhost:3000` in the browser.

## Environment Variables

The default `dashboard/.env.local` is:

```env
NEXT_PUBLIC_API_URL=/api/v1
NEXT_PUBLIC_WS_URL=ws://localhost:8000/api/v1
API_PROXY_TARGET=http://localhost:8000
DASHBOARD_API_KEY=
```

Meaning:

- `NEXT_PUBLIC_API_URL=/api/v1`: the browser calls the frontend on the same origin, and Next.js proxies the request
- `API_PROXY_TARGET=http://localhost:8000`: the actual backend target used by the Next.js proxy
- `NEXT_PUBLIC_WS_URL=ws://localhost:8000/api/v1`: the WebSocket base URL used for task streams and notifications
- `DASHBOARD_API_KEY`: optional shared secret. When set on the FastAPI backend, `/api/v1/*` HTTP endpoints require it; the Next.js server-side proxy injects it without exposing it to browser code.

If the API runs on another port, update both `API_PROXY_TARGET` and `NEXT_PUBLIC_WS_URL`.

For manual startup with API protection enabled, set the same `DASHBOARD_API_KEY` in the API process and the dashboard web process. The root `./scripts/dashboard.sh` helper reads it from the root `.env` for the web process when the shell has not already exported it.

## Why `Countries unavailable` Appears

This usually means one of the following:

1. The FastAPI backend is not running, so `localhost:8000` cannot be reached.
2. The backend is running, but database initialization is incomplete, and `/api/v1/countries` is failing.
3. The proxy target in `dashboard/.env.local` does not match the actual API address.

Check these first:

```bash
./scripts/dashboard.sh status
curl http://localhost:8000/api/v1/health
curl http://localhost:8000/api/v1/countries
```

If health checks pass but the country list still fails, verify database initialization:

```bash
python main.py init-database
python scripts/full_rebuild_database.py --yes
```

## Common Issues

### 1. Frontend lock file error

This usually means an earlier `next dev` process did not exit cleanly. Start with:

```bash
./scripts/dashboard.sh stop web
./scripts/dashboard.sh start web
```

### 2. Port 3000 or 8000 is already in use

The script will refuse to overwrite an unknown process. Stop the conflicting process first, then start the dashboard again.

### 3. The API is running but the page is still empty

Confirm these endpoints return data:

```bash
curl http://localhost:8000/api/v1/health
curl http://localhost:8000/api/v1/countries
```

If the `countries` endpoint returns a database-related error, the problem is not in the dashboard UI. It is in backend dependencies or database initialization.
