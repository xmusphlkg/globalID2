# GIDS Cloudflare Subscriptions

Cloudflare Worker + D1 service for GIDS subscription management.

## 1. Configure `.env`

All subscription Worker configuration is read from the repository root `.env`.
Do not hand-edit Wrangler configuration. The helper generates an owner-readable
`wrangler.generated.jsonc`; it is ignored by Git and contains one explicit
`staging` or `production` environment plus a separate local-only baseline.

Required Cloudflare CLI values:

```env
CLOUDFLARE_API_TOKEN=...
CLOUDFLARE_ACCOUNT_ID=...
```

The API token must be scoped to the account that owns the D1 database and needs these account permissions:

- `D1 Edit`
- `Workers Scripts Edit` or `Workers Scripts Write`
- `Account Settings Read` is optional, but removes Wrangler's account lookup warning.

Required subscription Worker values:

```env
SUBSCRIPTIONS__WORKER_NAME=globalid-subscriptions
SUBSCRIPTIONS__COMPATIBILITY_DATE=2026-08-17
SUBSCRIPTIONS__WORKERS_DEV=false
SUBSCRIPTIONS__PUBLIC_BASE_URL=https://subscriptions.globalinfectiousdisease.com
SUBSCRIPTIONS__ALLOWED_ORIGINS=https://globalinfectiousdisease.com
SUBSCRIPTIONS_LOCAL__PUBLIC_BASE_URL=http://localhost:8787
SUBSCRIPTIONS_LOCAL__ALLOWED_ORIGINS=http://localhost:4321
SUBSCRIPTIONS_LOCAL__DEBUG_RETURN_TOKENS=false
SUBSCRIPTIONS__DEBUG_RETURN_TOKENS=false
SUBSCRIPTIONS__D1_BINDING=DB
SUBSCRIPTIONS__D1_DATABASE_NAME=your-d1-database-name
SUBSCRIPTIONS__D1_DATABASE_ID=your-d1-database-id
SUBSCRIPTIONS__REMOTE_ENVIRONMENT=production
SUBSCRIPTIONS__PENDING_EXPIRY_DAYS=14
SUBSCRIPTIONS__SUBMISSION_RATE_LIMIT_PER_HOUR=30
SUBSCRIPTIONS__CONFIRMATION_EMAIL_LIMIT_PER_10_MINUTES=2
SUBSCRIPTIONS__SITUATION_ALERT_BATCH_SIZE=20
SUBSCRIPTIONS__SITUATION_ALERT_MAX_ATTEMPTS=5
SUBSCRIPTIONS__SITUATION_ALERT_RETENTION_DAYS=180
SUBSCRIPTIONS__SITUATION_ALERT_AUTOMATED_POLICY_ENABLED=false
SUBSCRIPTIONS__SITUATION_PUBLIC_ORIGINS=https://globalinfectiousdisease.com
SUBSCRIPTIONS__MAINTENANCE_CRON=*/5 * * * *
SUBSCRIPTIONS__LOG_SAMPLING_RATE=1
SUBSCRIPTIONS__TRACE_SAMPLING_RATE=0.05
```

Use the helper below for all subscription Worker commands:

```bash
cloudflare/subscriptions/scripts/wrangler-env.sh whoami
```

Local config generation and D1 migrations do not require Cloudflare
credentials. Remote commands require the environment as a positional argument;
the script never silently defaults a remote action to production.

Before any remote operation, run the complete local gate:

```bash
cd cloudflare/subscriptions
npm ci
npm run verify
state_dir="$(mktemp -d /tmp/gids-subscriptions-d1.XXXXXX)"
./scripts/wrangler-env.sh migrate-local "$state_dir"
./scripts/wrangler-env.sh dry-run production
./scripts/wrangler-env.sh startup production
```

The last two commands create unique ignored artifact directories and print their
paths. They compile and profile only; they do not deploy, migrate a remote D1
database, upload secrets, or send mail.

## 2. Set secrets

Use long random values for `TOKEN_SIGNING_SECRET` and `ADMIN_API_TOKEN`.

```env
SUBSCRIPTIONS__TOKEN_SIGNING_SECRET=replace-with-a-long-random-value
SUBSCRIPTIONS__ADMIN_API_TOKEN=replace-with-a-long-random-value
SUBSCRIPTIONS__SITUATION_ALERT_INGEST_TOKEN=replace-with-a-separate-long-random-value
SUBSCRIPTIONS__EMAIL_DELIVERY_INGEST_TOKEN=replace-with-another-long-random-value
```

`SITUATION_ALERT_INGEST_TOKEN` is a dedicated machine-to-machine secret. Do not
reuse the dashboard admin token, expose it to a browser, or include it in a
Situation report.

`EMAIL_DELIVERY_INGEST_TOKEN` independently authenticates delivery, bounce,
defer, and complaint callbacks from the approved SMTP provider adapter. The
callback stores only bounded event identifiers and aggregate state; it never
stores a provider webhook payload. Hard bounces and complaints suppress the
matched contact automatically.

If Turnstile is enabled on the subscribe form:

```env
SUBSCRIPTIONS__TURNSTILE_SECRET_KEY=...
```

The subscription Worker can send confirmation emails through the same SMTP
settings used by the local automation service. If `SUBSCRIPTIONS__SMTP_*`
values are empty, the helper script falls back to `AUTOMATION__SMTP_*`.

```env
AUTOMATION__SMTP_HOST=email-smtp.ap-southeast-1.amazonaws.com
AUTOMATION__SMTP_PORT=587
AUTOMATION__SMTP_USERNAME=...
AUTOMATION__SMTP_PASSWORD=...
AUTOMATION__SMTP_FROM_EMAIL=noreply@globalinfectiousdisease.com
AUTOMATION__SMTP_USE_TLS=true
SUBSCRIPTIONS__SMTP_FROM_NAME=GIDS Alerts
```

Upload runtime secrets from `.env`:

```bash
SUBSCRIPTIONS__ALLOW_SECRET_SYNC=production \
  cloudflare/subscriptions/scripts/wrangler-env.sh sync-secrets production
```

This uploads `TOKEN_SIGNING_SECRET`, `ADMIN_API_TOKEN`, optional Turnstile,
`SITUATION_ALERT_INGEST_TOKEN`, and `EMAIL_DELIVERY_INGEST_TOKEN`, plus SMTP
username/password as Worker secrets.
SMTP host, port, sender address, sender name, and TLS mode are written to the
generated Wrangler config from `.env`.

This Worker currently uses the separately approved SMTP provider. It does not
declare a Cloudflare `send_email` binding. Cloudflare Email Service is limited
to transactional mail and must not be substituted for the recurring Research
Radar campaign path. A future native-binding adoption should be a separately
reviewed change with a restricted sender allowlist; local `remote: true` email
bindings are intentionally absent because they send real mail.

The SMTP adapter adds a correlation `Message-ID` and stores the provider's
accepted message identifier when available. Configure the provider's event
adapter to POST this bounded contract to
`/api/internal/email-delivery-events` with the dedicated bearer token:

```json
{
  "provider": "approved-smtp-provider",
  "event_id": "provider-event-unique-id",
  "provider_message_id": "provider-message-id",
  "correlation_id": "optional-gids-delivery-id",
  "event_type": "delivered",
  "occurred_at": "2026-08-27T10:00:00Z",
  "error_code": ""
}
```

Allowed event types are `delivered`, `deferred`, `bounced`, and `complained`.
The provider adapter must map only terminal/hard bounces to `bounced`; temporary
delivery failures map to `deferred`.
Events are idempotent by `provider` + `event_id`. Delivery aggregates and acceptance rate
are available from the protected `GET /api/admin/stats` endpoint.

### Optional Cloudflare Queue acceleration

The verified Situation alert path always writes to a D1 outbox before returning.
For faster fan-out, create a Queue and a dead-letter Queue in the same account,
then configure:

```env
SUBSCRIPTIONS__SITUATION_ALERT_QUEUE_NAME=globalid-situation-alerts
SUBSCRIPTIONS__SITUATION_ALERT_DEAD_LETTER_QUEUE=globalid-situation-alerts-dlq
```

The generated Worker config binds the producer as `SITUATION_ALERT_QUEUE` and
configures this Worker as its consumer. Queue messages contain only this
non-personal routing contract:

```json
{
  "schema_version": "situation-alert-job.v1",
  "event_id": "4e0c2d44-0ea7-4b76-9809-7aebd2cc9f19"
}
```

If the binding or Queue is temporarily unavailable, the request remains safely
queued in D1 and the five-minute scheduled task continues draining it.

## 3. Plan, back up, and apply D1 migrations

First inspect pending migrations (remote, read-only):

```bash
cloudflare/subscriptions/scripts/wrangler-env.sh migration-plan production
```

The mutating command requires an exact one-shot gate. It exports a portable SQL
backup into the ignored `backups/` directory before applying migrations; D1
also captures its platform backup when migrations are applied.

```bash
SUBSCRIPTIONS__ALLOW_REMOTE_MIGRATION=production \
  cloudflare/subscriptions/scripts/wrangler-env.sh migrate-remote production
```

Record the printed backup path, the migration list, deployment version, and the
UTC change time. If rollback is required, stop traffic-changing operations and
use `wrangler d1 time-travel info` to choose a point immediately before the
change, then run `wrangler d1 time-travel restore` only after explicit incident
approval. A Worker rollback does not roll back D1 schema or data; coordinate the
two operations and verify `/health` plus a read-only subscription query.

## 4. Sync form options

The subscribe page reads available lists, countries, diseases, Research Radar
topics, study types, and peer-review statuses from D1 via
`GET /api/subscriptions/options`. Migration
`0008_research_radar_subscriptions.sql` installs the controlled Research Radar
options; after site data is regenerated, sync the current generated country and
disease metadata into D1:

```bash
SUBSCRIPTIONS__ALLOW_REMOTE_OPTION_SYNC=production \
  cloudflare/subscriptions/scripts/wrangler-env.sh sync-options-remote production
```

This command reads:

- `astro-site/src/data/meta.json`
- `astro-site/src/data/diseases/index.json`

Then it upserts rows into `subscription_filter_options`. The webpage does not
need to be edited when those options change.

## 5. Deploy the Worker

```bash
SUBSCRIPTIONS__ALLOW_DEPLOY=production \
  cloudflare/subscriptions/scripts/wrangler-env.sh deploy production
```

Deployment uses Wrangler strict mode and the named environment. Afterward,
record `wrangler deployments status`, test `/health`, confirm the scheduled
trigger and bindings in the dashboard, and inspect structured logs before
enabling an upstream dispatcher. Roll back Worker code with
`wrangler rollback <VERSION_ID>`; never assume that also reverses D1.

## Custom Domain Security

If `SUBSCRIPTIONS__PUBLIC_BASE_URL` uses a custom domain such as
`https://subscriptions.globalinfectiousdisease.com`, make sure Cloudflare security rules do not challenge API requests.

Recommended dashboard rule:

```text
Expression:
(http.host eq "subscriptions.globalinfectiousdisease.com" and
 (http.request.uri.path eq "/health" or starts_with(http.request.uri.path, "/api/")))

Action:
Skip challenge/WAF/Bot protections for this API hostname and paths.
```

If `Skip` is not available on the current plan, use a Configuration Rule for this hostname and set Security Level to `Essentially Off`, with Browser Integrity Check disabled. Keep this scoped only to `subscriptions.globalinfectiousdisease.com`.

## API

Get public subscribe form options:

```bash
curl "$WORKER_URL/api/subscriptions/options"
```

Create a pending subscription:

```bash
curl -X POST "$WORKER_URL/api/subscriptions" \
  -H "content-type: application/json" \
  -d '{
    "email": "reader@example.com",
    "locale": "en",
    "frequency": "weekly",
    "list_codes": ["reports"],
    "countries": ["CN", "US"],
    "diseases": ["influenza"]
  }'
```

When SMTP is configured, the API sends a confirmation email immediately after
the D1 write succeeds. The response includes an `email` object:

```json
{
  "ok": true,
  "email": {
    "status": "sent",
    "provider": "smtp"
  }
}
```

Create a weekly Research Radar subscription with optional literature filters:

```bash
curl -X POST "$WORKER_URL/api/subscriptions" \
  -H "content-type: application/json" \
  -d '{
    "email": "reader@example.com",
    "locale": "zh",
    "frequency": "weekly",
    "list_codes": ["research_digest"],
    "research_topics": ["vaccination"],
    "study_types": ["systematic-review"],
    "peer_review_statuses": ["peer-reviewed"]
  }'
```

Get an audience for the existing Python SMTP sender:

```bash
curl -X POST "$WORKER_URL/api/admin/audience" \
  -H "authorization: Bearer $SUBSCRIPTIONS__ADMIN_API_TOKEN" \
  -H "content-type: application/json" \
  -d '{
    "list_code": "reports",
    "country": "CN",
    "disease": "influenza",
    "limit": 500
  }'
```

The audience response includes each recipient email and a signed `unsubscribe_url`.

For a Research Radar campaign, use `list_code=research_digest` and optionally
pass `research_topic`, `study_type`, or `peer_review_status`. Audience matching
includes subscribers with the same preference and subscribers who left that
filter empty (meaning all within the selected list). The same filters are
honoured by the Worker's campaign creation route.

### Weekly Research Brief campaign

`scripts/automation/dispatch_research_digest.py` is the maintained bridge from
the newest generated weekly brief to the existing admin campaign queue. It
accepts only published, quality-gated bilingual evidence, builds both English
and Chinese content, and places an explicit Research Radar article link (plus a
DOI link when available) under every finding. It does not fetch or print an
audience, email address, unsubscribe token, or raw delivery record.

Apply migration `0009_campaign_idempotency.sql` before using `--apply`. Then
regenerate and validate the static research release before preparing email:

```bash
venv/bin/python3 scripts/export_literature_site_data.py
venv/bin/python3 scripts/validate_research_release.py
venv/bin/python3 scripts/automation/dispatch_research_digest.py
```

The last command is a local-only dry-run by default. It validates the newest
`astro-site/src/data/research/weekly/YYYY-Www.json` and prints the exact
non-personal campaign payload without making a network request. Pin a file with
`--brief PATH` when replaying an older release.

Production configuration is read from environment variables:

```env
RESEARCH_DIGEST_WORKER_URL=https://subscriptions.example.com
SUBSCRIPTIONS__ADMIN_API_TOKEN=...
GIDS_PUBLIC_BASE_URL=https://globalinfectiousdisease.com
RESEARCH_DIGEST_DISPATCH_STRICT=true
```

`GIDS_PUBLIC_BASE_URL` defaults to the canonical public site. Strict mode
requires the Worker URL and token even for a dry-run, which makes configuration
drift fail a release job. It cannot inspect the Worker's SMTP secrets; verify
those separately before processing a campaign.

Queue the campaign without sending mail:

```bash
venv/bin/python3 scripts/automation/dispatch_research_digest.py \
  --strict-config --apply
```

Inspect the masked campaign detail through `GET /api/admin/notifications/{id}`.
When it is approved and SMTP configuration has been verified, explicitly drain
the queue in bounded batches:

```bash
venv/bin/python3 scripts/automation/dispatch_research_digest.py \
  --strict-config --apply --process --batch-size 20 --max-batches 500
```

The same command can be placed after research release validation in a scheduler
or publishing workflow. The campaign key is
`research-digest:YYYY-Www:r1`: an exact replay returns the original campaign
without duplicating deliveries, while changed content under that key fails with
HTTP 409. For an intentional corrected resend, review the new content and pass
`--revision r2` (then `r3`, and so on). The generated campaign is restricted to
active `research_digest` subscriptions whose frequency is exactly `weekly`, and
retains topic, disease, country, study-type, and peer-review preference matching.
Failed deliveries are terminal in the current campaign; do not silently retry
them. Correct the configuration or content, use a reviewed new revision, and
queue a new campaign.

### Verified Situation alert webhook

`POST /api/internal/situation-alerts` is the only automatic alert-ingest route.
It accepts only a published Situation report with a passed or warning-only
degraded gate and an analyzed `alert`/`strong` signal. A verified signal must
use one of two auditable paths:

- `verification_basis=automated_policy` is accepted only for
  `verification_policy_version=tiered_auto_v3.2`, current data, a passed effect
  threshold, completeness of at least 0.95, and at least one HTTPS evidence
  link. It also requires a structured `automation_decision` with status
  `auto_verified`, an artifact hash, no failed gate reasons, and a decision
  timestamp. `calibrated_statistical` requires a completed common-count
  `multi_horizon_gamma_poisson_v1` fit and `q_value <= 0.025` (the upstream
  group's calibrated threshold can be stricter). `official_corroboration`
  requires at least one matched official-event ID and the review gate
  `q_value <= 0.05`. `verified_by` must be exactly
  `policy:tiered_auto_v3.2`. The ingest handler returns `422` before D1 access
  unless `SITUATION_ALERT_AUTOMATED_POLICY_ENABLED=true` is also configured.
- `verification_basis=analyst_review` accepts current or lagged publishable
  data, including a transparently identified fallback fit. Its policy version
  must be `null`, and `verified_by` must be an opaque reviewer account ID, not
  a name or email address.

```bash
curl -X POST "$WORKER_URL/api/internal/situation-alerts" \
  -H "authorization: Bearer $SUBSCRIPTIONS__SITUATION_ALERT_INGEST_TOKEN" \
  -H "content-type: application/json" \
  -d '{
    "schema_version": "situation-alert.v1",
    "idempotency_key": "situation-v3-daily-2026-08-17-r8:signal-123",
    "report": {
      "report_id": "situation-v3-daily-2026-08-17-r8",
      "as_of": "2026-08-17T12:00:00Z",
      "quality_gate_status": "passed",
      "publication_status": "published",
      "public_url": "https://globalinfectiousdisease.com/situation/2026-08-17/"
    },
    "signal": {
      "signal_id": "signal-123",
      "analysis_status": "analyzed",
      "anomaly_state": "alert",
      "signal_type": "statistical_signal",
      "temporal_relevance": "current",
      "data_status": "current",
      "completeness": 0.98,
      "q_value": 0.005,
      "model": "multi_horizon_gamma_poisson_v1",
      "fit_status": "completed",
      "detector_tier": "common_count",
      "effect_threshold_passed": true,
      "verification_status": "verified",
      "verification_basis": "automated_policy",
      "verification_policy_version": "tiered_auto_v3.2",
      "automation_decision": {
        "status": "auto_verified",
        "basis": "calibrated_statistical",
        "policy_version": "tiered_auto_v3.2",
        "calibration_hash": "sha256-calibration-artifact",
        "gate_reasons": [],
        "matched_event_ids": [],
        "decided_at": "2026-08-17T11:55:00Z"
      },
      "verified_by": "policy:tiered_auto_v3.2",
      "verified_at": "2026-08-17T11:55:00Z",
      "observed_at": "2026-08-16T00:00:00Z",
      "title": "Influenza surveillance signal",
      "summary": "A calibrated-policy eligible increase was observed.",
      "countries": ["US"],
      "diseases": ["influenza"],
      "evidence_urls": ["https://www.cdc.gov/example"]
    }
  }'
```

The first accepted request returns `202`. An exact replay returns `200` with
`"duplicate": true`; reuse of the same idempotency key for different content
returns `409`. The service never sends from an unpersisted webhook payload.

Only active, confirmed `alerts` subscriptions with `frequency=instant` are
eligible. Country, disease, severity, and `report_type=situation` filters are
applied before a unique delivery row is created. Empty event geography or
disease values do not bypass a subscriber's corresponding filter.

The general `POST /api/admin/notifications` campaign route and
`POST /api/admin/audience` recipient export both reject the `alerts` list.
This prevents an arbitrary manual campaign or external sender from bypassing
the published/analyzed/verified Situation contract; use `reports` or
`weekly_digest` for non-alert announcements.

#### Production dispatch boundary

The upstream release dispatcher is
`scripts/automation/dispatch_situation_alerts.py`. It sends analyst-reviewed
signals and fully structured `tiered_auto_v3.2` decisions. Legacy or malformed
automated policies fail before any request is sent. The Worker-side
`SITUATION_ALERT_AUTOMATED_POLICY_ENABLED` flag is an independent deployment
kill switch and must remain false until the corresponding canary/live rollout
is approved; enabling either side alone is insufficient.

Configure the upstream release environment, not browser-visible build
variables:

```env
SITUATION_ALERT_WORKER_URL=https://subscriptions.globalinfectiousdisease.com
SITUATION_PUBLIC_REPORT_URL=https://globalinfectiousdisease.com/situation/
SITUATION_ALERT_INGEST_TOKEN=the-same-dedicated-ingest-secret-held-by-the-worker
SITUATION_ALERT_TIMEOUT_SECONDS=15
SITUATION_ALERT_DISPATCH_STRICT=true
```

`SITUATION_ALERT_WORKER_URL` must be a clean HTTPS origin. The dispatcher adds
`/api/internal/situation-alerts`; URLs containing credentials, query strings,
fragments, or another path are rejected. The public report URL and every
evidence URL must also use HTTPS. The bearer token is read from the environment
only and is never included in command arguments or error details.

After a production Pages deployment has been verified, dispatch the exact
report from that deployed artifact:

```bash
venv/bin/python scripts/automation/dispatch_situation_alerts.py \
  --report astro-site/dist/site-data/situation/v3/latest.json
```

One request is made per eligible analyst-reviewed signal. Its idempotency key
is a stable SHA-256 digest of `report_id` and `signal_id`, so replaying a release
is safe. HTTP, timeout, response-contract, or signal-contract failure exits
non-zero. Missing endpoint, public URL, or token exits successfully with an
explicit `configuration_missing` skip in non-strict environments; setting
`SITUATION_ALERT_DISPATCH_STRICT=true` (or passing `--strict-config`) makes the
same condition fail the release.

Inspect alert progress, verification basis, and policy version without
returning recipient addresses or reviewer IDs:

```bash
curl "$WORKER_URL/api/admin/situation-alerts?limit=50" \
  -H "authorization: Bearer $SUBSCRIPTIONS__ADMIN_API_TOKEN"
```

Manually drain a batch from the D1 outbox:

```bash
curl -X POST "$WORKER_URL/api/admin/situation-alerts/process" \
  -H "authorization: Bearer $SUBSCRIPTIONS__ADMIN_API_TOKEN"
```

Run maintenance manually:

```bash
curl -X POST "$WORKER_URL/api/admin/maintenance" \
  -H "authorization: Bearer $SUBSCRIPTIONS__ADMIN_API_TOKEN"
```

Get subscription health stats:

```bash
curl "$WORKER_URL/api/admin/stats" \
  -H "authorization: Bearer $SUBSCRIPTIONS__ADMIN_API_TOKEN"
```

List subscriptions for local dashboard/admin review:

```bash
curl "$WORKER_URL/api/admin/subscriptions?status=pending&limit=50" \
  -H "authorization: Bearer $SUBSCRIPTIONS__ADMIN_API_TOKEN"
```

The local dashboard proxies these admin calls through FastAPI under
`/api/v1/subscriptions/*`, so the browser never receives
`SUBSCRIPTIONS__ADMIN_API_TOKEN`. The dashboard also exposes a manual option
sync action that runs:

```bash
SUBSCRIPTIONS__ALLOW_REMOTE_OPTION_SYNC=production \
  cloudflare/subscriptions/scripts/wrangler-env.sh sync-options-remote production
```

During data release, `SUBSCRIPTIONS__SYNC_OPTIONS_ON_RELEASE=auto` syncs D1
country and disease options when the subscription D1 settings are present.

## Notes

- Subscriptions start as `pending` and become `active` when the recipient clicks the signed confirmation link.
- Confirmation email attempts are recorded in `transactional_email_deliveries`.
- Pending subscriptions older than `SUBSCRIPTIONS__PENDING_EXPIRY_DAYS` are marked `expired` by the maintenance task.
- Subscription attempts are rate-limited per IP, and confirmation emails are rate-limited per contact to reduce abuse.
- For local testing only, set `SUBSCRIPTIONS__DEBUG_RETURN_TOKENS=true` in `.env`; the subscribe API will then return confirmation URLs.
- Empty filters mean "all". For example, a subscription with no country filters receives all countries.
- Situation email failures retry after approximately 1 minute, 5 minutes, 30
  minutes, 2 hours, and 6 hours by default. After
  `SUBSCRIPTIONS__SITUATION_ALERT_MAX_ATTEMPTS`, the D1 delivery is marked
  `dead_letter`; a configured Cloudflare Queue also uses its separate DLQ.
- Delivery is at-least-once. Event and recipient idempotency prevent ordinary
  webhook or Queue replays from duplicating messages; a rare Worker failure
  after SMTP accepts a message but before D1 records success can still cause a
  retry. The SMTP provider message log should be used to reconcile that case.
- Unsubscribing immediately marks pending/retrying Situation deliveries as
  skipped. A delivery already handed to SMTP cannot be recalled.
- Queue messages, logs, and admin alert-list responses contain no email address.
  Delivery errors redact email addresses and IP addresses before persistence.
  Completed Situation alert payloads and their transactional delivery records
  are removed after `SUBSCRIPTIONS__SITUATION_ALERT_RETENTION_DAYS` (180 by
  default).
- Situation alert wording deliberately describes verified statistical
  surveillance signals, states whether verification came from analyst review
  or `tiered_auto_v3.2`, and does not infer a public-health risk rating.
