# Unattended ingestion resilience

Surveillance ingestion is designed to run without routine operator action while
remaining fail-closed. A completed task means that the official source and all
configured quality gates were satisfied; an empty or structurally incompatible
payload is never converted into a successful refresh.

## Production audit baseline

The read-only audit on 2026-08-17 found 21 enabled ingestion jobs. Among the 300
most recent crawl tasks, 270 completed, 29 failed, and one was cancelled. The
evidence also exposed three operational gaps:

- `retry_threshold` controlled notification wording but did not cause a failed
  scheduled crawl to run again. Transient HK TLS EOF failures therefore waited
  until the next daily schedule.
- failed pipelines left their `crawl_runs` audit row in `running`, making source
  health look permanently active (93 historical rows, including 28 from the
  preceding 14 days, were present at audit time);
- China report processing collected per-report exceptions and returned an empty
  result, so a selected official report could complete with zero usable data.

These gaps are closed by `ingestion-transient-v1`.

## Automatic retry policy

Only tasks created by an enabled automation job with `scheduled_trigger=true`
are eligible. Manual tasks never retry automatically.

Safe retry categories are an allow-list:

- connection reset/refused/closed, DNS and timeout failures;
- TLS unexpected EOF and bounded HTTP client retry exhaustion;
- HTTP 429 and 5xx upstream service failures;
- an impossible empty official data payload after a valid source request.

The following remain terminal and immediately follow the normal alert path:

- source contract, selector, schema, or table-discovery drift;
- quality-gate, mapping, Registry, duplicate-identity, or validation failures;
- unsupported country/configuration and programming errors;
- authentication, authorization, HTTP 401/403, or missing credentials;
- any failure without a recognized safe signature.

`retry_threshold` is the total failure threshold. With the default value `3`,
the original failure can schedule at most two retries. The default delays are 5
and 10 minutes, capped at one hour, and can be configured with:

```dotenv
AUTOMATION__AUTO_RETRY_BASE_DELAY_SECONDS=300
AUTOMATION__AUTO_RETRY_MAX_DELAY_SECONDS=3600
```

The same task is parked in `retrying`, then atomically returned to `queued` when
due. A competing crawl for the same country blocks requeue until it finishes.
The parked retry also blocks a duplicate scheduled task. Intermediate failures
do not alert; exhaustion or a terminal classification does. Retry state and the
last classification are persisted in `Task.metadata.ingestion_automatic_retry`.

## Empty, stale, and drift behavior

- Official-source adapters continue to reject empty normalized batches.
- China processing now fails when every selected report produces no usable
  dataset. Partial success remains visible and does not discard valid reports.
- Any pipeline exception finalizes the current `crawl_runs` row as `failed` and
  records `failed_closed=true`; it no longer leaves a new permanent `running`
  audit row.
- Worker-startup recovery closes the run belonging to an interrupted crawl as
  `cancelled`, so a process restart cannot create another audit-row orphan.
- The automation snapshot exposes `health_status`, `health_reason`,
  `last_success_at`, `last_success_age_minutes`, and `stale_after_minutes` for
  every job. Health is `recovering` while a retry is parked, `failed` after a
  terminal task, and `stale` after two expected cadences without success.

Existing historical `running` rows are deliberately not rewritten by the new
runtime because the legacy table has no task foreign key. New executions are
closed correctly. A separate reviewed reconciliation can clean historical rows
without guessing during normal scheduling.

## Safety invariants

- Retry never changes `force`, `process`, source scope, revision window, mapping,
  or quality configuration.
- The original task and its diagnostic workbook are reused; no duplicate source
  job is created.
- Empty payloads fail first. Retry is recovery from a failure, never a way to
  relabel empty data as healthy.
- Quality and contract failures never enter a retry loop.
