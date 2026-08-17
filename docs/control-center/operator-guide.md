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

Scheduled and upstream-triggered data releases automatically retry recognized
transient external failures with a persisted exponential backoff. A release in
`retrying` is waiting for its durable deadline and does not require the manual
Retry action. Code, contract/gate, configuration, and credential failures remain
terminal. See [Data release automatic recovery](../DATA_RELEASE_RESILIENCE.md).
