# GIDS Cloudflare Subscriptions

Cloudflare Worker + D1 service for GIDS subscription management.

## 1. Configure `.env`

All subscription Worker configuration is read from the repository root `.env`.
Do not hand-edit `wrangler.toml`; the helper script generates `wrangler.generated.toml` from `.env` and that file is ignored by Git.

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
SUBSCRIPTIONS__COMPATIBILITY_DATE=2026-05-01
SUBSCRIPTIONS__WORKERS_DEV=true
SUBSCRIPTIONS__PUBLIC_BASE_URL=https://globalid-subscriptions.<your-subdomain>.workers.dev
SUBSCRIPTIONS__ALLOWED_ORIGINS=https://globalinfectiousdisease.com,http://localhost:4321
SUBSCRIPTIONS__DEBUG_RETURN_TOKENS=false
SUBSCRIPTIONS__D1_BINDING=DB
SUBSCRIPTIONS__D1_DATABASE_NAME=your-d1-database-name
SUBSCRIPTIONS__D1_DATABASE_ID=your-d1-database-id
SUBSCRIPTIONS__PENDING_EXPIRY_DAYS=14
SUBSCRIPTIONS__SUBMISSION_RATE_LIMIT_PER_HOUR=30
SUBSCRIPTIONS__CONFIRMATION_EMAIL_LIMIT_PER_10_MINUTES=2
```

Use the helper below for all subscription Worker commands:

```bash
cloudflare/subscriptions/scripts/wrangler-env.sh whoami
```

## 2. Set secrets

Use long random values for `TOKEN_SIGNING_SECRET` and `ADMIN_API_TOKEN`.

```env
SUBSCRIPTIONS__TOKEN_SIGNING_SECRET=replace-with-a-long-random-value
SUBSCRIPTIONS__ADMIN_API_TOKEN=replace-with-a-long-random-value
```

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
cloudflare/subscriptions/scripts/wrangler-env.sh sync-secrets
```

This uploads `TOKEN_SIGNING_SECRET`, `ADMIN_API_TOKEN`, optional Turnstile, and
SMTP username/password as Worker secrets. SMTP host, port, sender address, sender
name, and TLS mode are written to the generated Wrangler config from `.env`.

## 3. Apply the D1 migration

```bash
cloudflare/subscriptions/scripts/wrangler-env.sh migrate-remote
```

## 4. Sync form options

The subscribe page reads available lists, countries, and diseases from D1 via
`GET /api/subscriptions/options`. After site data is regenerated, sync the
current generated country and disease metadata into D1:

```bash
cloudflare/subscriptions/scripts/wrangler-env.sh sync-options-remote
```

This command reads:

- `astro-site/src/data/meta.json`
- `astro-site/src/data/diseases/index.json`

Then it upserts rows into `subscription_filter_options`. The webpage does not
need to be edited when those options change.

## 5. Deploy the Worker

```bash
cloudflare/subscriptions/scripts/wrangler-env.sh deploy
```

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

## Notes

- Subscriptions start as `pending` and become `active` when the recipient clicks the signed confirmation link.
- Confirmation email attempts are recorded in `transactional_email_deliveries`.
- Pending subscriptions older than `SUBSCRIPTIONS__PENDING_EXPIRY_DAYS` are marked `expired` by the maintenance task.
- Subscription attempts are rate-limited per IP, and confirmation emails are rate-limited per contact to reduce abuse.
- For local testing only, set `SUBSCRIPTIONS__DEBUG_RETURN_TOKENS=true` in `.env`; the subscribe API will then return confirmation URLs.
- Empty filters mean "all". For example, a subscription with no country filters receives all countries.
