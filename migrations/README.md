# Database migrations

The baseline revision represents the schema that existed before the control
center refactor. Existing installations must run the preflight command before
stamping the baseline:

```bash
python scripts/control_plane_migrate.py preflight
alembic stamp 0001_control_plane_baseline
alembic upgrade head
```

New control-plane schema changes must be additive, reversible, and covered by
an upgrade/downgrade test. The API, worker, and scheduler never run migrations
implicitly during startup.
