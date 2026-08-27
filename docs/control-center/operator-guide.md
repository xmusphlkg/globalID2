# Operator Guide

## Workspaces

### Overview

Use Overview as the first response surface. It combines queue counts, failed work, live API/scheduler/worker heartbeats, enabled schedules, recent tasks, operational action items, and Events & Signals. A missing scheduler or worker heartbeat is treated as a blocker.

### Ingestion & Tasks

- **Sources** shows current ingestion coverage and the most recent source pipeline state.
- **Task Runs** lists asynchronous work. Open a task to inspect workbook events, logs, progress, output, cancellation state, and errors.
- **Schedules** unifies ingestion and release automation. Use the `kind` URL filter to share a stable filtered view. `Run now` creates or references an asynchronous task.
- **Runtime** reads Redis TTL heartbeats. An instance disappears automatically after its heartbeat expires.

### Data Governance

Country Analytics, Diseases & Series, Quality, Explorer, Knowledge, Disease Mapping, and Disease Audit preserve the existing disease semantics. Select the country in the top bar; the selection is encoded as `country` in the URL.

### AI & Reports

Use AI Generation to enqueue generation work, Agent Runs and Interactions to inspect execution, Reports for review, Data Releases for publication, Subscriptions for audience data, and Campaigns for notification delivery.

### Settings

Integration settings cover SMTP, GitHub, Cloudflare, site publication, and runtime defaults. AI Providers & Models controls model routing. Secret values are server-side and the UI only displays a masked value or configuration status.

## Common operations

1. Confirm `/health/ready` is healthy and Runtime shows an API, scheduler, and at least one worker.
2. Start a crawl from Sources or trigger an ingestion schedule.
3. Follow the returned task UUID in Task Runs. Live updates use SSE; polling continues if Redis or SSE is unavailable.
4. Resolve failed tasks from Overview action items. Retry is only available for failed or cancelled work.
5. Run release checks before triggering a data release.

Cancellation is cooperative for running work and immediate for queued work. Retry clears cancellation metadata and requeues the same stable task UUID so audit history is retained.

## Background-task recovery

The worker and scheduler are singleton services backed by Redis leases. Every
claimed task records its worker owner and a heartbeat in `tasks.metadata`. A
worker continuously scans for expired task leases. `sync_literature`,
`enrich_literature`, and `discover_literature_gaps` are idempotent and are
automatically returned to `queued` until the task's `max_retries` is exhausted;
other task types are conservatively cancelled for operator review.

Use the read-only check before any restart:

```bash
venv/bin/python scripts/check_task_runtime.py
systemctl status globalid-dashboard-worker.service globalid-dashboard-scheduler.service
```

For a planned restart, use the fail-closed drain command instead of composing
individual `systemctl` calls:

```bash
venv/bin/python scripts/restart_task_runtime.py --include-api
```

It freezes the scheduler first, immediately asks the worker to stop claiming
new work, waits for claimed tasks to drain, optionally restarts the API, then
starts the worker and waits for its Redis heartbeat before starting the
scheduler. If systemd had to kill the old worker, the command compare-deletes
only that exact Redis owner and recovers only tasks persisted with that owner.
A delayed cleanup cannot remove a replacement worker lease. Any failure leaves
the scheduler stopped and prints a machine-readable `maintenance_failed` event;
inspect the runtime before resuming it.

The worker unit allows up to 15 minutes for an already-claimed task to finish
cooperatively. Do not start a second worker by manually bypassing the Redis
lease.
The worker cgroup also applies `MemoryHigh=4G` and `MemoryMax=6G`; a memory-limit
restart should be treated like a hard worker failure and audited with the same
recovery commands.

`/health/ready` is degraded when PostgreSQL or the Redis runtime registry is
unavailable, or when either the worker or scheduler heartbeat is missing. It
also reports queued/running counts and the age of the oldest queued task.

For a hard worker failure:

1. Stop the scheduler first so it cannot add work, then stop the worker.
2. Inspect without writing: `venv/bin/python scripts/recover_stale_tasks.py`.
3. Normally restart the worker and let its automatic recovery sweep run. For an
   explicit offline repair, run `venv/bin/python scripts/recover_stale_tasks.py
   --apply` while the worker remains stopped.
4. Start the worker, wait for its heartbeat, then start the scheduler. Confirm
   `scripts/check_task_runtime.py` returns exit code 0.

The recovery CLI refuses to write while a worker heartbeat is live or when the
runtime registry cannot be verified. `--force` exists for independently audited
emergencies only. Do not run `scripts/dashboard.sh` and the systemd stack at the
same time; the API service now fails its preflight with the owning PID when its
port already has a listener.

Scheduled and upstream-triggered data releases automatically retry recognized
transient external failures with a persisted exponential backoff. A release in
`retrying` is waiting for its durable deadline and does not require the manual
Retry action. Code, contract/gate, configuration, and credential failures remain
terminal. See [Data release automatic recovery](../DATA_RELEASE_RESILIENCE.md).
