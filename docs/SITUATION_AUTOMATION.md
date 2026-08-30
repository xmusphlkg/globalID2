# Situation Room automated release

`.github/workflows/situation-room-release.yml` provides the production-oriented,
fail-closed automation path for Situation Room. It runs every day at 03:17 UTC
and can also be started with **Run workflow**.

## Release sequence

The workflow calls `scripts/automation/run_situation_release.py`, which owns one
ordered sequence:

1. `scripts/update_situation_room.py` fetches the live external event/context
   sources and runs deterministic Situation analysis against the normalized
   surveillance series in PostgreSQL.
2. `scripts/export_situation_v3_contracts.py` regenerates the schema, OpenAPI,
   and TypeScript contracts. A Git status check fails the release when committed
   contracts are stale, deleted, or unexpectedly untracked.
3. `scripts/generate_site_data.py` exports the current database-backed static
   data.
4. `npm run build:astro` creates `astro-site/dist`.
5. `scripts/validate_situation_release.py` verifies the publication pointer,
   Pydantic contract, analysis quality gate, public/shadow indexing behavior,
   latest JSON aliases, and sitemap.

Each command has a bounded timeout and its own log. The orchestrator writes an
atomic `manifest.json` after every step. `deployment_ready` becomes `true` only
after all six steps pass. The final manifest contains a deterministic SHA-256
tree digest binding every artifact path, byte size, and file digest; symbolic
links and missing release-critical files are rejected.

The production deploy is a separate job that depends on the successful build
job and downloads that exact artifact. Before Wrangler runs,
`verify_situation_artifact.py` recomputes the complete tree inventory and also
binds the manifest to the current Actions run ID, attempt, source commit, and
exact six-step sequence. After Wrangler returns successfully,
`verify_situation_deployment.py` polls the public v3 JSON endpoint with bounded
backoff until its bytes match the gated artifact. Alert dispatch cannot start
until this public consistency probe passes. A failed, timed-out, cancelled,
substituted, partially uploaded, or stale-cache build therefore cannot be
reported as a successful production release.

After a verified production deployment, a third job reads that exact artifact
and dispatches only independently analyst-reviewed Situation signals to the
subscription Worker. Statistical `automated_policy` signals are blocked before
any network request because the current calibration does not support unattended
publication or email. Per-report/per-signal idempotency prevents duplicate mail
when a workflow is retried. Transport errors, HTTP 408/425/429, HTTP 5xx, and
invalid transient Worker responses receive bounded exponential retries;
permanent 4xx or contract rejections fail immediately.

## Live-ingestion boundary

This workflow refreshes the live event/context feeds consumed directly by the
Situation pipeline. The normalized disease time series remain owned by the
existing Control Center ingestion scheduler and production PostgreSQL. GitHub
Actions does not duplicate jurisdiction-specific crawlers or create an
ephemeral production database. Before enabling the schedule, keep the Control
Center scheduler and workers healthy and make the production database securely
reachable from the selected runner.

For installations that do not expose PostgreSQL to GitHub-hosted runners, use a
self-hosted runner with network access and restrict the workflow/environment to
that runner. Do not weaken database firewall or TLS settings merely to enable
this workflow.

## GitHub configuration

Create these Actions secrets for the build job:

| Name | Required | Purpose |
| --- | --- | --- |
| `SITUATION_DATABASE_URL` | yes | Async PostgreSQL URL for normalized surveillance data |
| `SITUATION_HISTORY_DATABASE_URL` | yes | Async PostgreSQL URL for immutable Situation runs, reports, reviews, and publication pointers |

Use `postgresql+asyncpg://...` URLs and require TLS according to the database
provider. The workflow never prints the values and never passes them as command
arguments.

Create a GitHub environment named `situation-production`. Configure these
environment secrets and repository/environment variables only if automatic
Cloudflare deployment is desired:

| Name | Kind | Required for deploy | Purpose |
| --- | --- | --- | --- |
| `CLOUDFLARE_API_TOKEN` | environment secret | yes | Pages edit token scoped to the intended account/project |
| `CLOUDFLARE_ACCOUNT_ID` | environment secret | yes | Cloudflare account identifier |
| `CLOUDFLARE_PROJECT_NAME` | variable | yes | Existing Pages project used by the repository's Wrangler release mechanism |
| `SITUATION_PUBLIC_DATA_URL` | variable | yes | Public HTTPS URL for `site-data/situation/v3/latest.json`, used for exact post-deploy verification |
| `SITUATION_AUTO_DEPLOY` | variable | no | Set to `true` for unattended deploys after successful scheduled gates |
| `PUBLIC_GA4_MEASUREMENT_ID` | variable | no | Public analytics identifier used at Astro build time |
| `PUBLIC_SUBSCRIPTIONS_API_BASE` | variable | no | Public subscription API base included in the static build |
| `SITUATION_ALERT_WORKER_URL` | variable | alerts | HTTPS subscription Worker origin, without a path or credentials |
| `SITUATION_PUBLIC_REPORT_URL` | variable | alerts | HTTPS public Situation report URL placed in email |
| `SITUATION_ALERT_INGEST_TOKEN` | environment secret | alerts | Dedicated machine-to-machine ingest token, matching the Worker secret |

Recommended environment controls are protected-branch restriction to `master`
and least-privilege Cloudflare tokens. Do not require a human reviewer for each
routine scheduled deployment; code, migration, credential, and release-policy
changes still go through the normal repository review process.

With `SITUATION_AUTO_DEPLOY=true`, a successful scheduled gate deploys without
operator intervention. With the variable false or unset, scheduled runs are
artifact-only rehearsals. A manual run on `master` can request deployment with
the `deploy` checkbox. Before enabling unattended deploys, confirm that the
normal Data Release automation publishes changed public download partitions so
pages do not go live before the files they link to are available.

Choose exactly one production deployment owner. When the existing Control
Center `site-release` job remains enabled, leave `SITUATION_AUTO_DEPLOY` false
and use this workflow as an independently gated rehearsal/failover. To make
GitHub Actions the unattended production owner, first disable the Control
Center release schedule and then enable `SITUATION_AUTO_DEPLOY`. Running both
schedules as production writers would create redundant daily deploys even
though their concurrency controls prevent corruption within each scheduler.

## Concurrency and retention

- Workflow concurrency is global to the production Situation writer.
- `cancel-in-progress: false` queues a new run instead of terminating a database
  write or pointer update midway.
- The whole build job is limited to 90 minutes; each internal step also has its
  own shorter timeout.
- Run manifests and logs are retained for 21 days.
- Deployment-ready static artifacts are retained for 7 days.
- Generated data and build output remain outside the source repository, in line
  with `docs/DATA_VERSIONING.md`.

## Continuous quality and migration smoke

`.github/workflows/project-quality.yml` is the merge-time quality gate for the
whole application. Every pull request and push to `master` or `development`
runs the complete offline Python suite, generated contract drift checks,
Dashboard type/tests/build, Astro type and deterministic tests, Subscription
Worker tests/typecheck, and a PostgreSQL migration smoke job in parallel. The
Astro job also runs performance-budget unit tests and a real static build; the
build itself executes the route/chunk/font performance gate.

Generated site data remains untracked. For clean-checkout CI only,
`prepare_site_build_fixture.py` creates the smallest deterministic input set
needed to compile every route. It refuses non-CI use unless explicitly opted
in and never overwrites an existing export. The fixture proves build mechanics
and performance budgets; it is never used by the scheduled production release,
which always runs the real database-backed export and publication gate.

The migration job creates a disposable database whose name must end in
`_migration_smoke`, requires the explicit
`MIGRATION_SMOKE_ALLOW_DESTRUCTIVE=1` opt-in, validates a single linear Alembic
head, and round-trips the latest revision before running `alembic check`. The
scheduled production release never applies migrations; only a revision that
has already passed this isolated smoke test should be rolled out through the
normal database deployment procedure.

`tests/run_tests.sh` is the canonical local/CI Python entry point. Its default
`all` mode runs the complete offline pytest suite and propagates every failure;
it no longer suppresses integration stderr or treats a failed integration run
as success. Live-network and real-email probes remain explicit operator
actions because they mutate or depend on external systems.

## Operator checks

Validate the automation definition without contacting live sources:

```bash
venv/bin/python scripts/automation/run_situation_release.py \
  --dry-run \
  --run-id local-audit
venv/bin/python -m pytest -q tests/unit/test_situation_automation.py
venv/bin/python scripts/automation/smoke_migrations.py --graph-only
```

For an artifact-only production rehearsal, leave `SITUATION_AUTO_DEPLOY`
unset/false and start the workflow manually without checking `deploy`. Inspect
`manifest.json`, every step log, and the downloaded `dist` before enabling the
production environment.

Database migrations are intentionally not applied by this scheduled workflow.
Run reviewed migrations through the normal coordinated deployment procedure
before a new application revision is allowed to execute here.
