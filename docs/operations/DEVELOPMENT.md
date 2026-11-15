# Development and local operations

Start with [Quick start](../../QUICKSTART.md). Run commands from the repository root unless a block changes directories.

## Configuration

Copy the example environment file and adjust values for your machine and model provider:

```bash
cp .env.example .env
```

The shipped example includes these categories:

- application settings: `APP_ENV`, `APP_NAME`, `DEBUG`, `LOG_LEVEL`
- runtime database: `DATABASE_URL`, `DATABASE_URL_SYNC`
- performance: `MAX_PARALLEL_TASKS`, `MAX_CRAWLER_CONCURRENT`, `TASK_WORKER_CONCURRENCY`, `TASK_WORKER_AI_CONCURRENCY`
- AI bootstrap defaults: `DEFAULT_AI_PROVIDER`, `DEFAULT_MODEL`, `AI__MODEL_CHAIN_RAW`
- paths: `DATA_DIR`, `LOG_DIR`, `CONFIG_DIR`

Dashboard work uses separate general, release, and AI organizer lanes.
`TASK_WORKER_AI_CONCURRENCY` bounds active AI business organizers, including
parents awaiting model results, and is clamped by `TASK_WORKER_AI_CONCURRENCY_MIN`
and `TASK_WORKER_CONCURRENCY`. Model HTTP concurrency is admitted separately by
the model center's per-route durable leases. Adding model capacity does not add
business task stacks. `AI__IDLE_BACKLOG_REVIEW_BUFFER_SIZE` sets the maximum open
literature workflow budget (default/maximum 512). The supply target follows twice
the available text-model permits, with a floor of 64 inside the saved budget;
an explicit smaller budget remains binding across restarts. When the AI organizer
budget exceeds one, background work
leaves one slot for reports. Health checks have a separate single-organizer lane.
With `TASK_WORKER_AI_DYNAMIC_CONCURRENCY_ENABLED=true`, no routable models pauses
new AI business tasks; health checks can still recover the routes. Disabling it
keeps the configured organizer budget. Situation tasks that explicitly skip AI
use the ordinary collection lane. Runtime model occupancy comes from unexpired
database leases across all dispatchers; it measures occupied permits, including
request preparation, rather than live HTTP connections.
The general-worker systemd drop-in starts the independent AI worker. The AI
worker starts 12 auxiliary model dispatchers and publishes model readiness. The example configuration allows 12 AI organizers with
`TASK_WORKER_CONCURRENCY=16`; organizer slots and dispatcher processes do not
multiply the model center's global route limits. Service templates budget up to
85 database pool connections across these dispatchers, the general worker, AI
worker, API, scheduler, and historical rescreen service. Keep additional clients within the
database's connection limit when changing these settings. The installer also
installs the managed service drop-ins so the dispatcher count survives reinstalls.
The [AI runtime architecture and cutover notes](../audits/AI_MODEL_CENTER_ARCHITECTURE_REVIEW_2026-11-10.md#重构实施记录2026-11-10)
describe activity leases, explicit retry generations, durable review workflows,
and the coordinated migration from the previous scheduler.
`TASK_WORKER_TASK_HEARTBEAT_SECONDS`, `TASK_WORKER_STALE_TASK_SECONDS`, and
`TASK_WORKER_RECOVERY_SCAN_SECONDS` control task-lease recovery; keep the stale threshold
comfortably above the heartbeat interval. `SCHEDULER_WORKER_GRACE_SECONDS` bounds how long
the scheduler may remain active without either worker role's heartbeat.

For disease knowledge building, runtime routing comes from the dashboard model
center. Environment model values only seed the model-center tables during first
initialization or an explicit forced bootstrap. The worker deterministically
rotates preferred model-center routes per disease/language. Requests wait in the
durable queue until a route meets the request's requirements; provider failures
are retried or moved to another route by the dispatcher. Each review requires a
different underlying model unless that data line explicitly allows a single
model. Changing providers alone does not make two identically named models
independent reviewers.

The Situation module reads official reports (WHO Disease Outbreak News, ECDC
CDTR, Africa CDC, PAHO and others) in full and extracts alerts: one disease in
one place, with the counts and dates the report states and one sentence quoted
from it. Two independent models read each document (the original PDF when the
model route accepts files, extracted text otherwise), and an alert is public
only after the reviewer accepts it. Full text stays internal; the public export
carries structured facts, the quote and the link. The normal operational
sequence is:

```bash
venv/bin/alembic upgrade head
venv/bin/python scripts/run_situation_notices.py
venv/bin/python scripts/generate_site_data.py
cd astro-site && npm run build:astro
cd .. && venv/bin/python scripts/validate_situation_release.py --site-dir astro-site/dist
```

See [`docs/operations/SITUATION_AUTOMATION.md`](SITUATION_AUTOMATION.md) for the
pipeline stages, export layout and release checks.

The Python configuration layer also supports provider-specific credentials such as:

- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `GLM_API_KEY`
- `QIANWEN_API_KEY`
- `AZURE_API_KEY`
- `CUSTOM_API_KEY`

### AI-assisted disease duplicate review

The disease duplicate audit can run fully offline, or ask an OpenAI-compatible
model to classify review candidates:

```bash
python3 scripts/audit_disease_duplicates.py --fail-on-high
python3 scripts/audit_disease_duplicates.py --ai-review --ai-output reports/disease_duplicate_ai_review.json
```

`--ai-review` uses the same model-center routing used by the management
dashboard. Configure providers and model routes in `/ai/models`; the audit will
use active, routable model-center routes and respect cooldown/unavailable state.

The AI layer is advisory only: it recommends `merge`, `keep_separate`, or
`needs_human_review`, but it does not edit mapping CSVs automatically.

The management dashboard also exposes this workflow at `/ai/disease-audit`.
That dashboard entry uses the configured AI model center routes rather than
direct environment variables, and it can review both duplicate disease concepts
and newly observed unmapped disease terms from current source data.

If you want email delivery for generated reports, configure SMTP values as well.

## Boot Autostart

If you want the database containers, dashboard API, dashboard worker, dashboard web, and Astro site to come back automatically after the machine boots, this repository now includes systemd templates and an installer script.

1. Make sure dependencies are already installed once on this machine:

```bash
docker compose up -d
cd dashboard && npm install
cd ../astro-site && npm install
cd ..
```

2. Install and enable the services:

```bash
sudo ./scripts/install_systemd_services.sh --enable --start
```

This installs:

- `globalid-docker.service`
- `globalid-dashboard-api.service`
- `globalid-dashboard-worker.service` (`TASK_WORKER_ROLE=general`)
- `globalid-ai-worker.service` (`TASK_WORKER_ROLE=ai`)
- `globalid-dashboard-scheduler.service`
- `globalid-dashboard-web.service`
- `globalid-site.service`
- `globalid-stack.target`

The AI worker owns AI business organizers, the main model dispatcher, and the
model-runtime readiness signal. Twelve auxiliary dispatchers follow its lifecycle.
General ingestion/export work has its own worker process and Redis lease; restarting
it does not stop the AI worker. The checked-in database pool ceilings total 85
connections (including API 10 + 6 and AI worker 8 + 4), below PostgreSQL's 100-connection limit.

Useful commands:

```bash
systemctl status globalid-stack.target
journalctl -u globalid-dashboard-api.service -f
venv/bin/python scripts/check_task_runtime.py
journalctl -u globalid-dashboard-web.service -f
journalctl -u globalid-site.service -f
sudo ./scripts/install_systemd_services.sh --uninstall
```

Optional `.env` knobs for autostart behavior:

- `GLOBALID_API_PORT=8000`
- `GLOBALID_DASHBOARD_PORT=3000`
- `GLOBALID_SITE_PORT=4321`
- `GLOBALID_DASHBOARD_BUILD_ON_START=1` to rebuild the dashboard before starting it
- `GLOBALID_SITE_BUILD_ON_START=1` to rebuild the Astro site before serving it
- `GLOBALID_SITE_REGENERATE_ON_START=1` to rerun `scripts/generate_site_data.py` from the database on boot before rebuilding the site

The site service serves the generated `astro-site/dist/` directory over Python's built-in static HTTP server. If you change site data but do not set `GLOBALID_SITE_REGENERATE_ON_START=1`, the service will continue serving the last built output.

Generated data is deliberately excluded from the code repository. Data Release regenerates
site JSON from PostgreSQL, publishes validated time-partitioned CSV/JSON/XLSX files to the dedicated data
repository's `main` branch, builds Astro, and deploys the build without committing any
generated files back to the code branch. See `docs/operations/DATA_VERSIONING.md` for the repository and
retention boundaries.
