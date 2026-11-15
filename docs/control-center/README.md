# GIDS Control Center

The control center is the single-administrator operational surface for GIDS. It is organized into five workspaces: Overview, Ingestion & Tasks, Data Governance, AI & Reports, and Settings.

## Documentation

- [Operator Guide](operator-guide.md)
- [API Guide](api-guide.md)
- [Architecture](architecture.md)
- [Deployment and Rollback](deployment-and-rollback.md)
- [Troubleshooting](troubleshooting.md)
- [Extension Guide](extension-guide.md)

The browser only calls the same-origin Next.js BFF. The BFF injects the shared API key; credentials are never placed in browser environment variables or API payloads.

## Run locally

Use [Quick start](../../QUICKSTART.md) to start the API, general/AI workers,
scheduler and Next.js app. The dashboard is at `http://localhost:3000`; the API
normally listens on `http://localhost:8000`. Set the server-only
`API_PROXY_TARGET` when the API address differs.

The full container stack is `docker/dashboard-full-stack.yml`. Managed systemd
installation, logs and restart commands are documented in
[Development and local operations](../operations/DEVELOPMENT.md).
