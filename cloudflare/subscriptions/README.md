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
SUBSCRIPTIONS__SITUATION_ALERT_BATCH_SIZE=3
SUBSCRIPTIONS__SITUATION_ALERT_MAX_ATTEMPTS=5
SUBSCRIPTIONS__SITUATION_ALERT_RETENTION_DAYS=180
SUBSCRIPTIONS__SITUATION_ALERT_AUTOMATED_POLICY_ENABLED=false
SUBSCRIPTIONS__SITUATION_PUBLIC_ORIGINS=https://globalinfectiousdisease.com
SUBSCRIPTIONS__MAINTENANCE_CRON=*/5 * * * *
SUBSCRIPTIONS__LOG_SAMPLING_RATE=1
SUBSCRIPTIONS__TRACE_SAMPLING_RATE=0.05
SUBSCRIPTIONS__REPORT_DIGEST_ENABLED=false
SUBSCRIPTIONS__REPORT_DIGEST_START_WEEK=
SUBSCRIPTIONS__RESEARCH_DIGEST_ENABLED=false
SUBSCRIPTIONS__RESEARCH_DIGEST_START_WEEK=
SUBSCRIPTIONS__NOTICE_ALERTS_ENABLED=false
SUBSCRIPTIONS__NOTICE_ALERTS_START_DATE=
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

### Optional Amazon SES / SNS feedback adapter

The SES SMTP adapter understands Amazon's `250 Ok <message-id>` response.
An optional SNS webhook is included but **disabled by default**:

```env
SUBSCRIPTIONS__SES_SNS_FEEDBACK_ENABLED=false
SUBSCRIPTIONS__SES_SNS_TOPIC_ARN=
SUBSCRIPTIONS__SMTP_SES_CONFIGURATION_SET=
```

Apply migration `0015_ses_feedback_pending.sql` before deploying this code.
To enable later, provide one exact topic ARN in the same AWS region as SES,
set the flag to `true`, and redeploy the Worker. The SNS HTTPS endpoint is
`https://subscriptions.globalinfectiousdisease.com/api/internal/ses-sns-feedback`.
It checks the configured exact TopicArn, SNS canonical RSA signature and SNS
signing certificate obtained over HTTPS from the SNS hostname in the topic's
region. Redirects, other hosts, unexpected certificate paths, and oversized
bodies are rejected. Signature versions 1 and 2 are verified; configure SNS
to use version 2 (SHA-256) where possible. A valid subscription confirmation
is followed only when its signed HTTPS URL points to that same regional SNS
endpoint with matching topic and token. Restrict the SNS topic's publish policy
to the intended SES source and AWS account; an authorized SNS publisher can
create messages trusted as events by this endpoint.

Set `SUBSCRIPTIONS__SMTP_SES_CONFIGURATION_SET` only to an existing approved
SES configuration set with an SNS event destination. It adds
`X-SES-CONFIGURATION-SET` and a `gids-delivery` UUID tag to SMTP mail. An
`X-GIDS-Delivery-ID` header is also emitted when an internal UUID is present;
if using SES identity notifications instead, enable inclusion of original
headers so early callbacks can correlate. These values never contain email
addresses or authentication secrets. Configure SNS for Delivery, Bounce,
Complaint and DeliveryDelay events. Permanent bounces and complaints suppress
the contact; transient/undetermined bounces and delivery delays do not.

After signature validation, an event arriving before the SMTP receipt is
matched by UUID tag/header or stored as bounded non-personal fields in D1.
The separately budgeted feedback task reconciles one due unmatched event per
Cron tick, backs off when still unmatched and expires it after 30 days with an
operator log event. It never stores raw SNS bodies or recipient addresses.
Historical messages without a tag/header and without a stored SES message ID
cannot be correlated automatically. SNS message IDs and the delivery-event
ledger make retries idempotent. Enabling this adapter does not create AWS
topics, policies, identities, configuration sets, subscriptions or destinations;
those must be configured and tested separately with an operator-controlled inbox.

#### Prepare and connect the dedicated AWS feedback resources

`scripts/setup-ses-feedback.py` prepares a dedicated topic and SES configuration
set in `ap-southeast-1`, matching the existing SES SMTP endpoint. It uses AWS CLI
v2 and the normal AWS credential provider chain; never put credentials into
source control, command arguments, or chat. The script does not send mail,
change the recipient whitelist, or enable full delivery. Existing resources
with conflicting policies, event destinations, or extra topic subscribers are
rejected rather than overwritten. It sets SNS signature version 2 and limits
SES publishing to this account and this exact configuration set.
Ensure the AWS CLI identity belongs to the same AWS account that owns the
existing SES SMTP sender; a correctly configured topic in a different account
will not collect its mail events. The resource policy follows the
[AWS SES SNS event-destination guidance](https://docs.aws.amazon.com/ses/latest/dg/event-publishing-add-event-destination-sns.html).

The AWS identity needs `sns:ListTopics`, `sns:CreateTopic`,
`sns:GetTopicAttributes`, `sns:SetTopicAttributes`,
`sns:ListSubscriptionsByTopic`, `sns:Subscribe`,
`ses:ListConfigurationSets`, `ses:CreateConfigurationSet`,
`ses:GetConfigurationSetEventDestinations`, and
`ses:CreateConfigurationSetEventDestination`. Configure IAM resources and
conditions to restrict access to the dedicated topic and configuration set;
list operations require the appropriate account-level scope. The script also
calls `sts:GetCallerIdentity` to identify the account. AWS CLI v2 and valid
credentials are prerequisites, not the SMTP username and password.

Run these stages from the repository root, preserving the current pilot mode:

1. `venv/bin/python cloudflare/subscriptions/scripts/setup-ses-feedback.py provision`.
   Record the exact topic ARN it prints. This creates or verifies the SNS topic,
   its SES-only source policy, SES configuration set and four event types.
2. In the ignored deployment environment, set
   `SUBSCRIPTIONS__SES_SNS_FEEDBACK_ENABLED=true` and the exact printed
   `SUBSCRIPTIONS__SES_SNS_TOPIC_ARN`. Leave
   `SUBSCRIPTIONS__SMTP_SES_CONFIGURATION_SET` empty for now. Deploy the Worker
   with the existing guarded Wrangler helper, retaining
   `SUBSCRIPTIONS__DELIVERY_MODE=pilot`. Ensure the SNS HTTPS endpoint is not
   blocked by a firewall challenge; SNS cannot solve interactive challenges.
3. Run `venv/bin/python cloudflare/subscriptions/scripts/setup-ses-feedback.py subscribe`.
   SNS sends a signed confirmation to the Worker. Rerun the command to check
   that the subscription is confirmed; investigate Worker and firewall logs if
   it remains pending. Do not enable the configuration set while confirmation
   is pending, because event delivery would be incomplete. The signed confirmation
   URL is handled by the Worker without AWS credentials, following the
   [SNS confirmation flow](https://docs.aws.amazon.com/sns/latest/dg/SendMessageToHttp.confirm.html).
4. Set `SUBSCRIPTIONS__SMTP_SES_CONFIGURATION_SET` to the printed configuration
   set name and redeploy the Worker, still in pilot mode. Send one controlled
   message to the authorized administrator inbox and confirm that its delivery
   event appears in the protected delivery ledger. Test bounce and complaint
   handling using SES mailbox simulator addresses only in a separate isolated
   test environment where they are explicitly allowlisted; never add simulator
   addresses to the production administrator whitelist. Monitor Worker errors
   and SNS delivery failures. A successful SMTP response alone does not prove
   that the SNS callback arrived.

These steps require live AWS authorization and a signed SNS notification to
validate end to end. Until then the feedback integration remains disabled;
messages sent before the configuration set is active do not gain feedback
retroactively.

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

The Cron task uses same-domain authenticated POSTs with
`User-Agent: GIDS-Release-Verifier/1.0`. An SNS webhook has an AWS-controlled
user agent. Before enabling either in production, verify that the exact
Custom Domain paths are reachable without an interactive security challenge:
test the five `/api/internal/scheduled/*` paths with the admin bearer in staging
and the SNS webhook with an AWS-signed staging notification. An authenticated
scheduled request processes real queued jobs, so inspect the target environment
and queue before invoking it. Verify separately that
unauthorized Cron requests fail and forged SNS requests are rejected by the
Worker. Check Cloudflare security events, structured
`subscription_scheduled_task_failed` and `ses_sns_feedback_rejected` logs, SNS
delivery failure metrics, and queue age for 403/5xx responses.

If a rule challenges legitimate server-to-server requests, first identify the
specific offending rule and apply the narrowest possible exception to the
affected exact hostname, path and rule. Keep other WAF and bot protections in
place; do not disable security for the entire hostname or `/api/` subtree.
Do not rely on a user-agent string for authorization: Cron still requires its
bearer secret and SNS requires the exact topic and cryptographic signature.

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
    "consent": true,
    "locale": "en",
    "frequency": "weekly",
    "list_codes": ["reports"],
    "countries": ["CN", "US"],
    "diseases": ["influenza"]
  }'
```

The API persists a pending change and confirmation outbox entry. An immediate
background attempt is followed by Cron recovery where needed. The generic
response does not reveal whether an address exists or whether SMTP accepted
the message; it does not mean that the subscription is already active:

```json
{
  "ok": true,
  "email": {
    "status": "pending",
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
    "consent": true,
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

### Weekly report digest

The report release pipeline can dispatch newly observed, verified public
reports to `reports` and `weekly_digest` as separate campaigns. Enable it on
the release host only after verifying the public R2 report manifest, Worker
credentials, and a persistent shared release outbox:

```env
SUBSCRIPTIONS__REPORT_DIGEST_ENABLED=true
SUBSCRIPTIONS__REPORT_DIGEST_START_WEEK=2026-W46
```

Choose a future ISO start week explicitly. The first successful observation
only establishes a baseline and does not mail the existing catalogue. Once a
full UTC week has completed, the next successful report or main-site release
can create that week's campaign; releases must continue for pending outbox
records to be retried. Subscriber targeting uses report metadata; verify the
public reports manifest and content before enabling it.

### Automatic research dispatch

The release pipeline can instead dispatch the most recently completed ISO week
after the exact release manifest is visible on the public site:

```env
SUBSCRIPTIONS__RESEARCH_DIGEST_ENABLED=true
SUBSCRIPTIONS__RESEARCH_DIGEST_START_WEEK=2026-W46
```

Set the start week deliberately: only that week and subsequent completed weeks
are eligible; the pipeline never backfills older issues. The Worker must expose
the `research_content_items_v1` health capability. Public-only immutable
outbox records live under `exports/subscription-outbox/research/` on the
release host. Pending records are retried on a later release; this local
outbox requires persistent storage and a single shared filesystem for all
release workers. Run a regular release to drain it, and monitor release errors.
The campaign key is
`research-digest:YYYY-Www:r1`: an exact replay returns the original campaign
without duplicating deliveries, while changed content under that key fails with
HTTP 409. For an intentional corrected resend, review the new content and pass
`--revision r2` (then `r3`, and so on). The generated campaign is restricted to
active `research_digest` subscriptions whose frequency is exactly `weekly`, and
matches each subscriber against at least one individual cited article across
their topic, disease, country, study-type, and peer-review preferences. The
body is the full issue, not an individually trimmed digest. Explicit SMTP 4xx
rejections may retry; ambiguous outcomes are marked unknown for reconciliation.
Never resend a whole campaign merely because a subset failed.

### Verified Situation alert webhook

`POST /api/internal/situation-alerts` accepts the current
`official-notice-alert.v1` event described below. It also retains the legacy
`situation-alert.v1` contract for already integrated callers. The legacy
contract accepts only a published Situation report with a passed or warning-only
degraded gate and an analyzed `alert`/`strong` signal. A legacy signal must
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

The current upstream is `src/services/data_release/notice_subscription.py`.
After the exact Pages release manifest is visible, it stages the public
official-notice records in a local durable outbox and sends them to the same
Worker endpoint under the separate `official-notice-alert.v1` schema. It does
not invent legacy statistical fields. It selects only published records whose
cross-model review verdict is `accept`, with event status new, imported,
ongoing, or ended. Status is included in the email as reported by the source.

Both automatic delivery paths are disabled by default. On the persistent
release host configure an explicit start date and the same dedicated ingest
secret as the Worker:

```env
SUBSCRIPTIONS__NOTICE_ALERTS_ENABLED=true
SUBSCRIPTIONS__NOTICE_ALERTS_START_DATE=2026-11-04
SUBSCRIPTIONS__SITUATION_ALERT_INGEST_TOKEN=...
SUBSCRIPTIONS__PUBLIC_BASE_URL=https://subscriptions.globalinfectiousdisease.com
GIDS_PUBLIC_BASE_URL=https://globalinfectiousdisease.com
```

Only records dated on or after the activation date and within the last seven
days are admitted as new. At most 100 new records are admitted per release;
larger sets fail for review. Pending records remain recoverable across weeks
if their content is still present in the deployed export. Replaying an accepted
event does not expand its original recipient set. The Worker health capability
`official_notice_alert_v1` must be present before sending. Public-only outbox
files in `exports/subscription-outbox/official-notices/` require persistent,
shared release-host storage; they are retried on a later release. SMTP delivery
is asynchronous through the Worker outbox and Cron.

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

- Subscriptions start as `pending` and become `active` only when the recipient submits the signed confirmation form. GET requests merely display the form, so automated mail link previews do not change consent.
- Confirmation email attempts are recorded in `transactional_email_deliveries`.
- Pending subscriptions older than `SUBSCRIPTIONS__PENDING_EXPIRY_DAYS` are marked `expired` by the maintenance task.
- Subscription attempts are rate-limited per IP, and confirmation emails are rate-limited per contact to reduce abuse.
- For local testing only, set `SUBSCRIPTIONS__DEBUG_RETURN_TOKENS=true` in `.env`; the subscribe API will then return confirmation URLs.

Only enabled content pipelines appear as available choices. Enable each
`SUBSCRIPTIONS__*_ENABLED` setting together with its activation watermark on
the release host, and redeploy the Worker with that same configuration so the
public options and server-side validation agree. New subscriptions support
English and Chinese, weekly report/research digests, and instant notice alerts;
existing unsupported historical frequencies are preserved rather than silently
changed. A delivery marked `unknown` requires provider feedback or operator
investigation and is not automatically retried.

Readers can request a short-lived, email-delivered management link at
`/api/subscriptions/manage` to edit one list, pause/resume it, or unsubscribe
all email updates. Management tokens have a separate purpose and are revoked
when a subscription is confirmed again or unsubscribed. Management-link
requests return a generic response immediately and send in the background;
unlike confirmation jobs, a management-link request is not durably retried if
the invocation is interrupted, so readers can request another link.

Each Cron tick dispatches maintenance, confirmation, campaign, and Situation
work through separate authenticated same-origin internal requests; this keeps
their D1 query budgets independent. The custom domain and admin token must be
reachable from the Worker, and WAF rules must not challenge these API routes.
Each confirmation invocation drains at most one queued email, each campaign
batch at most five, and each Situation batch at most three; a backlog therefore
may take multiple five-minute ticks. The optional Queue consumer handles one
message per invocation. Monitor task-failure logs and queue age, and adjust the
architecture rather than raising batch limits past D1's per-invocation budget.
- Empty filters mean "all". For example, a subscription with no country filters receives all countries.
- Explicit SMTP 4xx rejections are retried with backoff up to
  `SUBSCRIPTIONS__SITUATION_ALERT_MAX_ATTEMPTS`; 5xx is terminal. Ambiguous
  timeouts and expired claims become `unknown` and are not silently retried.
  Reconcile these using the provider message log and delivery callbacks.
- Unsubscribing immediately marks pending/retrying Situation deliveries as
  skipped. A delivery already handed to SMTP cannot be recalled.
- Queue messages, logs, and admin alert-list responses contain no email address.
  Delivery errors redact email addresses and IP addresses before persistence.
  Completed Situation alert payloads and their transactional delivery records
  are removed after `SUBSCRIPTIONS__SITUATION_ALERT_RETENTION_DAYS` (180 by
  default).
- Official-notice wording describes the published notice and its status;
  legacy statistical-signal wording retains its original verification basis.
  Neither asserts a public-health risk rating.

### 管理员试运行

`SUBSCRIPTIONS__DELIVERY_MODE` 默认是 `pilot`；Worker 在 SMTP 发送前以及队列领取前限制收件人，只有 secret `PILOT_RECIPIENTS` 中的管理员邮箱可收信。缺少配置或配置不匹配时默认不发送。同步现有的 `AUTOMATION__ADMIN_EMAILS_RAW` 到这个独立 secret（无需改动其他 secret）：

```bash
SUBSCRIPTIONS__ALLOW_SECRET_SYNC=production ./scripts/wrangler-env.sh sync-pilot production
```

修改管理员通知邮箱后需要重新同步。公开 options 的 `pilot_mode` 和 health 的 `delivery_mode` 显示当前模式，但不公开邮箱。试运行期间公众可以登记订阅并接收确认及管理邮件，内容投递仍限管理员；已经排队的非管理员历史内容投递会保留到全量模式。试运行新建的通知只为管理员建立投递记录，自动任务使用 `pilot:` 幂等键及独立水位，后续正式运行不会复用试运行任务。仅在试运行通过并明确决定开放后，将 `SUBSCRIPTIONS__DELIVERY_MODE` 显式设为 `full` 并部署；切换前检查待恢复的历史队列和自动任务水位。
