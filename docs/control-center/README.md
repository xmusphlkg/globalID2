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
