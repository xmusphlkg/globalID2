# GIDS Control Center

The control center contains the Next.js BFF/UI and FastAPI delivery layer.

```bash
./scripts/dashboard.sh start
./scripts/dashboard.sh status
./scripts/dashboard.sh logs api
./scripts/dashboard.sh logs scheduler
./scripts/dashboard.sh logs worker
```

Local endpoints:

- Dashboard: `http://localhost:3000`
- API: `http://localhost:8000`
- Swagger: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
- Liveness/readiness: `/health/live`, `/health/ready`

The browser calls only same-origin `/api/v1` Route Handlers. Configure `API_PROXY_TARGET` and the same server-side `DASHBOARD_API_KEY` for the web and API processes.

See the [Control Center documentation](../docs/control-center/README.md) for operating, API, architecture, deployment, troubleshooting, and extension guidance.
