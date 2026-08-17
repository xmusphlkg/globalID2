# Data release automatic recovery

Scheduled releases and releases triggered by upstream completion recover from
recognized transient external failures without operator action. The failed task
keeps the same UUID and moves to the durable `retrying` state. Its task metadata
contains `automatic_retry.next_attempt_at`; the release scheduler atomically
moves it back to `queued` when the exponential delay expires. A process restart
therefore does not lose the retry timer.

The default delays are 5, 10, and 20 minutes, capped at one hour. Configure the
policy with:

```dotenv
DATA_RELEASE__AUTO_RETRY_MAX_ATTEMPTS=3
DATA_RELEASE__AUTO_RETRY_BASE_DELAY_SECONDS=300
DATA_RELEASE__AUTO_RETRY_MAX_DELAY_SECONDS=3600
```

`retrying` is also a release reservation. A scheduled tick, an upstream task,
or another release job cannot enqueue a concurrent pipeline while that
reservation exists. Task workers do not claim a delayed `retrying` row; only the
release scheduler can make it runnable at the persisted deadline.

## Retry boundary

The classifier is deliberately allow-list based. It retries recognized timeout,
connection, DNS, rate-limit/HTTP 429, and upstream HTTP 5xx signatures only when
they occur in an external release stage: Situation source refresh, Git archive
or download publication, strict subscription sync, Cloudflare deployment, or
Cloudflare production verification. Post-deployment reviewed-alert dispatch is
also eligible for recognized Worker transport, HTTP 429, and HTTP 5xx failures;
its report/signal idempotency key prevents duplicate mail. A transient Cloudflare integration
preflight is also eligible when its retained diagnostic has a matching
signature.

The following remain terminal and alert normally:

- manual releases;
- syntax/import/type and other code failures;
- contract, schema, validation, and release-gate failures;
- missing/invalid configuration or credentials, including HTTP 401/403;
- unknown failures without a positive transient signature;
- failures after the configured retry cap.

The legacy automatic-failure cooldown applies after a terminal or exhausted
upstream-triggered failure. It does not replace the short retry sequence.

## Idempotency and audit

Retries reuse the original task UUID and persisted release identity. Only one
release pipeline can be active across all release jobs because generated
artifacts share directories. When a Cloudflare deploy command has completed but
production verification fails transiently, a checkpoint is committed before
verification; the next attempt reuses that deployment and retries verification
instead of deploying a duplicate.

Task workbook events expose `release_auto_retry_scheduled` and
`release_auto_retry_queued`, including classification, stage, attempt count,
delay, and deadline. Inspect `tasks.metadata.automatic_retry` for the durable
state and `tasks.metadata.release_checkpoints` for completed external effects.

If a task is terminal, fix the reported code/configuration/credential/gate issue
and use the normal manual retry action. Do not broaden the transient pattern list
to mask a deterministic failure.
