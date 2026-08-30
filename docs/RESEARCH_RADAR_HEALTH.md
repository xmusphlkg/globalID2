# Research Radar health check

`scripts/check_research_radar_health.py` is the read-only operational health
boundary for the Research Radar pipeline. It combines database run state with
the generated public release and the resumable metadata-backfill checkpoint,
then emits one machine-readable JSON object.

## Safety contract

- Database access is SELECT-only. The CLI opens a session directly and rolls
  the read transaction back; it never calls a mutation service or commits.
- The report contains aggregate counts, ratios, ages, statuses, thresholds,
  stable check codes, and a stable `next_action_code` for every check only.
- It never emits article IDs, titles, DOIs, task UUIDs, reviewer identity,
  exception text, source URLs, filesystem paths, credentials, or database
  connection details.
- Release-validator messages are reduced to category counts. Unexpected CLI
  exceptions collapse to `health_check_failed` instead of echoing exception
  text.
- Files are size-bounded before parsing (64 MiB for the release and 2 MiB for
  the checkpoint).

## Checks and SLOs

The report currently contains these stable check codes:

| Code | Coverage |
| --- | --- |
| `sync_freshness` | Age of the latest successful core-source run and its source watermark |
| `sync_failures_and_recovery` | Consecutive failures, a failed latest terminal run, recovered failures, and stale running runs |
| `sync_checkpoint` | Safe truncated resume plus catch-up requirement, remaining index span, and bounded-fetch efficiency |
| `catch_up_orchestration` | Accelerated catch-up state, durable next-run projection, backlog headroom, exact resume boundary, and stable operator action |
| `enabled_source_success` | Whether every currently enabled provider actually succeeded in the latest completed core run, using its persisted result/error/credential-skip counters |
| `metadata_backfill_checkpoint` | Backfill completion, provider failures, and stalled in-progress checkpoints |
| `classification_version` | Share of stored articles using the current classifier version |
| `metadata_provider_coverage` | OpenAlex and Unpaywall coverage among DOI-bearing stored articles |
| `public_bilingual_gate` | Bilingual-summary coverage at the actual public boundary |
| `release_validator` | Fail-closed release validation, exposed only as blocker category counts |
| `release_freshness` | Age of the generated public index |
| `weekly_digest` | Weekly brief freshness plus safe automatic/human-review provenance |
| `background_tasks` | Latest task health, stale active tasks, recent failures, and observed recoveries |
| `exception_backlog` | Current article/link/summary review objects, raw autopilot diagnostics, and evidence-gap errors |

Core synchronization runs are identified separately from
`research-radar-autopilot` runs. A recent summary/autopilot success therefore
cannot hide a failed or stale Crossref-based source synchronization.

### Enabled-source result truth

`LiteratureIngestRun.source` is a configuration-intent list written when a run
starts. It is not success evidence by itself. For the latest completed core
run, `enabled_source_success` evaluates a source as successful only when:

1. the source appears in that run's intent list;
2. all fields in that source's persisted count contract are present and valid
   non-negative integers;
3. all of its error counters are zero; and
4. credential-gated sources were not skipped for missing credentials.

The contracts cover Crossref result counters; Europe PMC, OpenAlex, and
Unpaywall enrichment/error counters; controlled-query result/query-error
counters; publisher-RSS result/feed-error counters; WHO IRIS OAI result/error
counters; bioRxiv/medRxiv result/error counters; and Springer Nature/Elsevier
result, error, and credential-skip counters. An explicit result count of zero
is healthy: a successful request may legitimately find no records. RSS still
requires at least one modified or not-modified feed response so an empty feed
configuration cannot appear healthy. Missing or malformed counters fail closed
instead of being coerced to zero.

The check exposes only provider labels and aggregate reason codes in
`source_results`: `success`, `not_in_latest_completed_run`,
`missing_count_contract`, `invalid_count_contract`, `unknown_count_contract`,
`provider_errors`, `skipped_credentials`, or `not_attempted`. Provider exception text,
endpoints, and credentials remain private.

Passing checks use `next_action_code=none`. Failing metadata coverage directs
operators to the dry-run backfill, provider failures to the failed provider
batch retry, stalled backfills to a controlled resume, and an over-budget
actionable review queue to the dry-run backlog governance/autopilot command.
These codes are intended for alerts and runbooks; they never contain object IDs
or exception text.

`sync_failures_and_recovery` uses
`next_action_code=reconcile_stale_ingest_runs_dry_run` when it sees an overdue
running ingest audit row. First inspect the aggregate-only plan:

```bash
venv/bin/python scripts/reconcile_stale_literature_ingest_runs.py \
  --stale-after-minutes 120
```

After verifying that the cutoff is older than the current run, repeat with
`--apply`. The apply path locks rows and rechecks all predicates, then marks
only still-running, cutoff-expired rows with no `task_uuid` as failed. Runs
created after migration are bound to their exact task and are finalized by the
same transaction that recovers that interrupted task; a replacement worker or
current task is not selected by timestamp.

An editorially reviewed weekly digest passes only when its public reviewer
projection satisfies `project_weekly_editorial_review`. A distinct
`ai_reviewed` digest passes only with valid content-bound AI review evidence
and no human reviewer; it is not an editorial signature. An unreviewed digest
passes only with `automatically_compiled_not_editorially_reviewed` and no
reviewer. Model/parse failures fail the `ENRICH_LITERATURE` task with
`weekly_brief_ai_review_failed_closed` while leaving site publication usable.

### Exception backlog counting

The thresholded exception backlog uses current database object state:

```text
raw_review_article_count
- deferred_review_article_count
- archived_decision_review_article_count
= current_review_article_count

raw_review_summary_count
- deferred_review_summary_count
- archived_decision_review_summary_count
= current_review_summary_count

current_review_article_count
+ current_review_link_count
+ current_review_summary_count
= uniqueish_exception_backlog
```

`combined_exception_backlog` is the same thresholded value. Each current
article, evidence link, or summary in `review` is counted once as a distinct
actionable review work item. Review rows are excluded only when their exact
persistent metadata path is `autopilot.decision == "defer"` or
`autopilot.decision == "archive"`. Article decisions are read from `metadata_`;
summary decisions are read from `generation_metadata`. Missing mappings,
malformed values, different casing, and unknown decisions remain fail-closed
in the backlog. Summary rows whose current status is `archived` are already
outside the raw review population and are exposed as `archived_summary_count`.

The calculation deliberately does not add the latest autopilot
`article_exceptions` counter to the current review-article count: those values
describe the same unresolved article population at two different observation
times and would deterministically double-count it.

For diagnosis, the report retains the raw latest-run counters as
`raw_latest_autopilot_article_exception_count`,
`raw_latest_autopilot_link_exception_count`, and
`raw_latest_autopilot_summary_exception_count`. The compatibility fields
`automation_exception_count` and `review_article_count` also remain available.
Raw latest-run deferred/archive counters, raw current review counts, and the
current metadata-derived deferred/archive counts are emitted separately so an
operator can audit every subtraction.
`raw_legacy_combined_exception_backlog` exposes the previous snapshot-plus-
current calculation for comparison only; it is not evaluated against
`max_exception_backlog`.

## Run it

From the repository root:

```bash
PYTHONPATH=. venv/bin/python scripts/check_research_radar_health.py --pretty
```

The command always writes JSON to stdout. Exit codes are:

| Code | Meaning |
| --- | --- |
| `0` | Healthy, or degraded with `--fail-on critical` |
| `1` | Degraded and configured to fail on warnings |
| `2` | Unhealthy: at least one critical check |
| `3` | The health check itself could not run safely |

For a dashboard that should display recoveries without failing its job:

```bash
PYTHONPATH=. venv/bin/python scripts/check_research_radar_health.py \
  --fail-on critical
```

For CI, retain the default `--fail-on warning`.

### Publisher coverage audit

The main health report verifies that every enabled source actually succeeded in
the latest completed run. Disabled credential-gated sources are intentionally
not treated as failures; an enabled credential-gated source that was skipped
for missing credentials is a warning, never a pass. To decide whether Springer
Nature or Elsevier credentials are actually needed, run the separate aggregate
audit:

```bash
PYTHONPATH=. venv/bin/python scripts/audit_literature_publisher_coverage.py \
  --recent-days 365 --pretty
```

This command is also read-only and network-free. It distinguishes PubMed/PMID
and Europe PMC enrichment from Crossref discovery, reports publisher and
journal coverage, and returns `publisher_apis_required_now=false` when recent
Springer/Elsevier records have at least 99% Crossref provenance and no stored
core-provenance gap. It never claims that the stored corpus is a publisher's
complete catalogue; that external gap is not observable from local records.

## Configure thresholds

Common SLOs can be overridden directly:

```bash
PYTHONPATH=. venv/bin/python scripts/check_research_radar_health.py \
  --max-sync-age-hours 8 \
  --max-source-lag-hours 16 \
  --min-openalex-coverage 0.95 \
  --min-unpaywall-coverage 0.95 \
  --max-exception-backlog 300
```

Every field in `HealthThresholds` can instead be supplied in a JSON object:

```json
{
  "max_sync_age_hours": 8,
  "max_source_lag_hours": 16,
  "max_consecutive_failures": 0,
  "max_stale_run_minutes": 90,
  "max_backfill_stalled_hours": 12,
  "min_classification_current_ratio": 0.995,
  "min_openalex_coverage": 0.95,
  "min_unpaywall_coverage": 0.95,
  "min_bilingual_public_ratio": 1.0,
  "min_public_articles": 1,
  "max_release_age_hours": 12,
  "max_release_blockers": 0,
  "max_digest_age_days": 10,
  "task_history_hours": 24,
  "max_stale_task_minutes": 120,
  "max_latest_failed_task_types": 0,
  "max_exception_backlog": 300,
  "max_evidence_gap_errors": 0,
  "run_history_limit": 50,
  "task_history_limit": 500
}
```

Use it with `--thresholds path/to/thresholds.json`. Unknown keys, negative
limits, and ratios outside `[0, 1]` fail safely with exit code 3. Direct CLI
flags take precedence over values in the JSON file.

## Alerting guidance

- Page on exit code 2: source sync is failed/stale, a checkpoint cannot resume,
  the classification or bilingual gate regressed, provider coverage is below
  SLO, the release is invalid/stale, or an operational backlog exceeded its
  bound.
- Ticket or annotate on exit code 1: the pipeline recovered after a failure,
  the latest completed core run predates a newly enabled source, or an enabled
  provider reported errors, an invalid/missing count contract, or a
  credential skip.
- Treat exit code 3 as monitor failure, not service health. Check database/file
  availability privately; the public result intentionally contains no detail.

The check verifies generated digest artifacts, not delivery at the external
email provider. Production campaign delivery requires the notification
worker's own delivery counters and alerting. Likewise, a completed one-time
metadata backfill may be old without being unhealthy; age is critical only
while the checkpoint remains `running`.
