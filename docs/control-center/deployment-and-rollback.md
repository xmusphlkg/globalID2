# Deployment and Rollback

## Coordinated deployment

The `/api/v1` contract is switched as one coordinated release. Do not deploy a mixed old/new frontend and API.

1. Record row counts for tasks, reports, diseases, source series, subscriptions, and schedule tables.
2. Back up PostgreSQL and verify the backup can be read.
3. Pause the scheduler process. Allow running workers to reach safe checkpoints; do not delete queued/running task rows.
4. Run the schema preflight:

   ```bash
   PYTHONPATH=. venv/bin/python scripts/control_plane_migrate.py preflight
   ```

5. For an existing database adopting Alembic, stamp the verified baseline once, then upgrade:

   ```bash
   PYTHONPATH=. venv/bin/alembic stamp 0001_control_plane_baseline
   PYTHONPATH=. venv/bin/alembic upgrade head
   ```

6. Deploy matching API, scheduler, worker, and Next.js images.
7. Verify `/health/live`, `/health/ready`, `/openapi.json`, `/api/v1/runtime/services`, and an authenticated `/api/v1/overview` request.
8. Run the core E2E smoke path and compare the recorded row counts before opening access.

Local process management:

```bash
./scripts/dashboard.sh start
./scripts/dashboard.sh status
./scripts/dashboard.sh logs scheduler
```

Container deployment uses `docker/dashboard-full-stack.yml`, which contains separate `api`, `scheduler`, `worker`, and `dashboard` services.

## Rollback

1. Pause the new scheduler.
2. Stop API, scheduler, worker, and dashboard containers from the new release.
3. Run `PYTHONPATH=. venv/bin/alembic downgrade 0001_control_plane_baseline` if the additive scheduled-state table must be removed. It is normally safe to retain during an application rollback.
4. Restore the database backup only if data/schema validation failed and the downgrade is insufficient. Restoring discards writes made after the backup and therefore requires explicit incident approval.
5. Deploy the previous coordinated image set.
6. Resume its scheduler and workers, verify queued/running tasks still exist, and compare row counts.

The control-plane migration does not rewrite crawler data, disease semantics, reports, or subscription Worker state.
