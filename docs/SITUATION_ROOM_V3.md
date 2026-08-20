# Situation Room v3

Situation Room v3 turns source-native surveillance series and attributable
official updates into revisioned analysis reports. A statistical anomaly is a
review signal; it is never presented as a public-health risk rating unless the
rating has an official or audited expert source.

## Contract

`src/services/situation_v3/contracts.py` is the source of truth for the public
and internal report contract. It generates:

- `configs/situation_room.v3.schema.json`
- `dashboard/openapi.json`
- `dashboard/src/generated/api.d.ts`
- `astro-site/src/generated/api.d.ts`

Regenerate all artifacts with:

```bash
venv/bin/python scripts/export_situation_v3_contracts.py
```

Every report contains report and method identity, code/config provenance,
source-and-cadence currency, coverage, unique signals, clustered official
events, separate context panels, source health, and the quality gate. Duplicate
`signal_id` values or inferred risk levels fail contract validation.

## Analysis flow

1. Official adapters fetch concurrently with bounded concurrency and retain
   source health independently.
2. PostgreSQL computes group counts, period-end age, and source/cadence cohort
   watermarks before transferring model history. A watermark requires at least
   80% of the active source cohort and is capped by cadence-specific maturity
   delays (daily 2 days, weekly 7 days, monthly 21 days after period end).
   Provisional rows newer than that cutoff remain recorded as latest available
   but do not enter the model. Insufficient and stale groups enter the immutable
   ledger without loading their full observations.
3. Geography aliases collapse to one source-native identity. The eligible
   frame is converted once and split deterministically across at most four
   model processes.
4. v3.2 keeps robust quasi-Poisson fitting as the parameter-estimation layer,
   then generates a deterministic Gamma-Poisson/negative-binomial predictive
   simulation. Weekly common-count series are evaluated over 1, 2, and 4 weeks;
   monthly series use 1 and 2 months. All horizons in a series share the same
   coefficient draws and process draws. The maximum standardized exceedance is
   calibrated against that joint simulation, producing one series-level p value
   rather than multiple independent votes. The seed includes stable series
   identity, analysis date, and detector version, so retries are reproducible.
   Common-count inference no longer uses the old fixed `2.0` variance multiplier.

   Expected counts at or below 20 remain in the rare-count tier. Its existing
   Poisson/Gamma-Poisson predictive tail remains the review model, while
   `hurdle_negative_binomial_v1` and `seasonal_empirical_v1` are recorded as
   shadow-only comparisons. Rare counts, fallback fits, daily data, rates, and
   data without a denominator cannot enter pure statistical auto-publication.
   CUSUM remains supporting evidence only.
5. Delayed feeds retain auditable model output as `watch`, receive q=1, and are
   isolated from the contemporary multiple-testing family. Remaining p values
   are adjusted with Benjamini–Hochberg within
   `(detector_tier, metric_type, cadence)`. Alerts also have to pass their
   metric effect gate.
   Percent/rate series with numerator and denominator use an exposure-offset
   model and still require a metric-specific effect gate; those without both
   components remain context-only. Disease/source-specific effect overrides are
   explicit configuration and replace any generic reappearance rule.
6. The v3.2 publication policy is fail-closed and supports `off`, `shadow`,
   `canary`, and `live`. Pure statistical automation is limited to calibrated
   weekly/monthly common counts with a completed primary model, current data,
   completeness at least 0.95, a source whitelist entry, the configured
   canonical source-data URL, an effect pass, and the calibrated group q gate.
   Rare counts, fallback models, and non-whitelisted sources require a matching
   authoritative official event within two source periods. Official matching
   verifies domain, exact disease, overlapping geography, and time, and records
   `lead`, `concurrent`, or `lag`; a historical event cannot validate a current
   signal. A detector/source-definition hash invalidates the calibration after
   any relevant configuration drift.

   Every signal receives a structured `automation_decision`, and every run
   persists that decision independently. Human reject, suppress, and correction
   decisions take precedence. Publication creates a new immutable revision;
   history is never deleted. Public language remains “statistical anomaly” or
   “officially correlated signal”. `public_health_risk` stays `not_assessed`
   unless an official agency or audited expert provides an attributable rating.

## Calibration and gold-standard events

The joint weekly/monthly calibration harness is deterministic:

```bash
venv/bin/python scripts/backtest_situation_v3.py
```

Its checked result is `docs/validation/situation-v3-backtest.json`. Seven
pre-registered scenarios cover weekly and monthly sustained 2x, sustained 1.5x,
and one-period 2x changes, plus a weekly rare-count 4x scenario. The default 128
batches yield 384 independent common-count complete-null families per cadence.
The automation threshold is selected from
`{0.0025, 0.005, 0.01, 0.015, 0.025}` only when its Wilson 95% false-publication
upper bound is at most 2.5%, sustained-2x sensitivity is at least 80%, median
delay is at most one period, and both weak-signal scenarios improve on v3.1 by
at least 15 percentage points. The minimum of 384 cannot be lowered by a CLI
flag.

Complete-null families rotate through separately reported zero-inflation,
cross-series correlation, missing-period, revision, structural-break, and
delayed-data stresses. Each artifact includes the config hash, calibration
definition hash, simulation protocol hash, seed, per-stratum Wilson intervals,
and weekly/monthly threshold tables. The `q <= 0.05` review gate is unchanged.

For a quick diagnostic that intentionally does not claim calibration precision:

```bash
venv/bin/python scripts/backtest_situation_v3.py \
  --batches 1 --series-per-class 2 \
  --minimum-complete-null-families 7 \
  --output /tmp/situation-v32-smoke.json
```

The smoke command still fails automation precision by design; it is only an
execution check. Formal artifacts also remain `not_supported` until a locked
real-event evaluation is supplied. Real labels use exact disease mapping,
overlapping geography, first official publication time, authoritative source,
confidence, and adjudication. Absence of an official record is not a negative;
uncertain cases remain `indeterminate`, and a negative label requires an
existing review decision or two distinct adjudicators. Chronological
development/tuning/locked-test assignment is 70/15/15 with a two-period embargo
at both boundaries.

Preview/import official positive labels and register a calibration artifact:

```bash
venv/bin/python scripts/import_situation_v3_event_labels.py events.json \
  --cadence weekly
venv/bin/python scripts/import_situation_v3_event_labels.py events.json \
  --cadence weekly --apply
venv/bin/python scripts/register_situation_v3_calibration.py \
  docs/validation/situation-v3-backtest.json
```

Both commands are read-only unless `--apply` is supplied. Registration
recomputes artifact/config/definition hashes and every numerical acceptance
gate rather than trusting an artifact's declared status.

### Current v3.2 calibration status

The checked offline artifact uses 128 batches and two null/two anomaly series
per batch. It provides 384 complete-null common-count families for each
cadence. Weekly simulation supports at most `q <= 0.0025` (3/384 automatic
false-publication families; Wilson upper 95% bound 2.27%; sustained-2x
sensitivity 95.31%), but the weekly group remains closed because no locked
real-event evaluation has been supplied. Monthly simulation also has 3/384 at
`q <= 0.0025` and sustained-2x sensitivity 84.77%, but it remains closed because
the one-period 2x improvement is -7.42 percentage points versus v3.1 and the
locked real-event evaluation is missing.

At the broader `q <= 0.05` review threshold, 58/896 stressed complete-null
families contain a discovery (6.47%; Wilson 95% interval 5.04%–8.28%). The
structural-break and correlated-series strata account for most of that excess,
so review-level calibration also remains failed. Production configuration is
therefore still `mode=off`, with the global kill switch active and both source
groups disabled. These results are evidence for further model work, not an
authorization to relax a gate.

## Storage and publication

Migration `0009_situation_v3` adds normalized analysis runs, signal results,
event clusters/items, period reports/members, review decisions, and publication
pointers. Migration `0010_situation_v32` adds the gold-standard event-label
library, immutable calibration registry, and run-level policy-decision ledger.
The dedicated history database stores the immutable report and signal archive.

Publication order is invariant:

1. stage the immutable analysis run and full ledger;
2. stage every structured automation decision;
3. pass the report quality gate;
4. commit the report to the history database;
5. commit the normalized primary rows;
6. atomically move the publication pointer.

An archive or primary transaction failure leaves the previous pointer public.
A retry reuses an identical history-only archive revision and safely completes
the primary transaction. Failed runs close as `failed`; gate failures close as
`gate_failed`.

Acquired event facts and operator review state are separate. Refreshes can add
source updates, but cannot overwrite publish, suppress, correct, or merge
decisions. Event updates with the same disease, overlapping geography, and a
45-day time relationship form one stable timeline. A merge is limited to
active clusters for the same disease and moves non-duplicate updates while
preserving the union geography and date range.

Only a newly staged analysis run can transition to `published`,
`completed_unchanged`, or `gate_failed`. Weekly and monthly members are
historical daily runs; aggregate publication must never rewrite their status.
Run IDs include a short input fingerprint so concurrent analyses in the same
second do not collide while identical retries remain deterministic.

## Period reports

Daily reports represent one eligible analysis run. Weekly and monthly reports
are generated only after a closed UTC period has eligible daily members. They
aggregate report membership and lifecycle state without resampling raw source
frequencies. A changed upstream member creates a new immutable revision.

Public routes are:

- `/situation/`
- `/situation/weekly/{period}/`
- `/situation/monthly/{period}/`
- `/site-data/situation/v3/latest.json`
- `/site-data/situation/v3/weekly/{period}.json`
- `/site-data/situation/v3/monthly/{period}.json`

Legacy human period paths issue a 301. `/site-data/situation/latest.json` is a
temporary byte-equivalent v3 alias and does not preserve the v2 field shape.

## Operations and verification

Run the analysis, export, build, and release gate with:

```bash
venv/bin/python scripts/update_situation_room.py
venv/bin/python scripts/generate_site_data.py
(cd astro-site && npm run build:astro)
venv/bin/python scripts/validate_situation_release.py --site-dir astro-site/dist
```

`--no-fetch-events` is an offline analysis mode. Every skipped external
adapter is emitted as `not_checked`, so a successful offline report is
`degraded` rather than falsely claiming fresh acquisition. It remains usable
for shadow review but should not be interpreted as a complete live-source run.

The internal UI at `/overview/events` exposes Overview, Runs, Signals, Sources,
Events, Reports, and Audit. All destructive review actions use confirmation
forms and append an audited decision. Rollback accepts only a gate-passed,
published report and moves the pointer without rewriting report history.

The statistical implementation is informed by robust Farrington-style
quasi-Poisson detection and is calibrated against repository simulations rather
than assuming theoretical defaults. Threshold review and signal validation
should remain part of the operating process.
