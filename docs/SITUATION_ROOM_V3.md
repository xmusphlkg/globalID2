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
4. Common and rare count series use robust quasi-Poisson regression with
   cadence-specific seasonality, optional trend, daily weekday effects,
   overdispersion, robust historical weights, and a one-sided predictive
   limit. Rare-count series replace the normal tail approximation with a
   Poisson/Gamma-Poisson predictive upper tail. Its process dispersion is the
   unweighted Pearson estimate (the robust fit weights are not reused to trim
   the tail), and its aggregate variance adds fitted-coefficient uncertainty
   by the delta method before matching the first two moments to a negative
   binomial. The coefficient covariance is rescaled from the robust process
   dispersion to the unweighted rare-tail dispersion, so parameter uncertainty
   is not left on the smaller trimmed scale. This rare-only correction does not
   change the established common-count path and does not apply the configurable
   normal-tail variance multiplier. If IRLS does not converge, count series may
   use the explicitly identified
   `seasonal_empirical_fallback_v1`; its seasonal sample counts and primary fit
   failure remain in diagnostics. An unavailable fallback produces no p/q
   value. CUSUM is recorded as supporting evidence only and never supplies a
   second decision vote.
5. Delayed feeds retain auditable model output as `watch`, receive q=1, and are
   isolated from the contemporary multiple-testing family. Remaining p values
   are adjusted with Benjamini–Hochberg within
   `(detector_tier, metric_type, cadence)`. Alerts also have to pass their
   metric effect gate.
   Percent/rate series with numerator and denominator use an exposure-offset
   model and still require a metric-specific effect gate; those without both
   components remain context-only. Disease/source-specific effect overrides are
   explicit configuration and replace any generic reappearance rule.
6. Automatic triage queues, deduplicates, and prepares evidence for eligible
   alert-level candidates, but the checked calibration does not yet support
   unattended statistical publication. Candidates remain private until
   independently verified. Rare-count and fallback candidates without an
   independent official match remain visible in the internal ledger but are
   held out of the analyst queue; an official match queues them for review.
   Delayed periods, incomplete evidence, or failed quality gates remain
   blocked. Verification and rejection are immutable audit decisions. An
   optional public-health risk level still needs an attributable rationale and
   evidence URL.
   `public_health_risk` otherwise remains `not_assessed`. Cached official
   events are attached before the latest operator decision so acquisition
   order cannot overwrite an audited expert conclusion.

The calibration harness is deterministic:

```bash
venv/bin/python scripts/backtest_situation_v3.py
```

Its checked result is `docs/validation/situation-v3-backtest.json`. The suite
uses four pre-registered weekly strata: a common-count sustained 2x outbreak,
a common-count sustained 1.5x increase, a common-count one-cycle 2x spike, and
a rare/low-count sustained 4x cluster. Each stratum retains seasonality, trend,
negative-binomial overdispersion, and a documented structural-zero component.
The common sustained 2x scenario is the primary sensitivity and v2-comparison
endpoint; the other strata disclose detector operating characteristics without
silently redefining the primary acceptance threshold.

The default protocol runs 20 independent batches in each of the four strata,
with 16 null and 16 anomaly-arm series per batch. This creates 80 independent
complete-null family trials instead of the former 10. Binary rates report 95%
Wilson score intervals. At zero complete-null discoveries, 80 trials put the
upper 95% bound below 5%; the report also records the minimum non-zero FDR step,
sample counts, seed, scenario manifest, and a hash of the complete simulation
protocol. A run is inconclusive (and the command exits non-zero) when its
complete-null family count is below the configured precision minimum, even if
the point estimate is zero.

For a quick diagnostic that intentionally does not claim calibration precision:

```bash
venv/bin/python scripts/backtest_situation_v3.py \
  --batches 1 --series-per-class 2 \
  --minimum-complete-null-families 4 \
  --output /tmp/situation-v3-smoke.json
```

The full report separately exposes complete-null family FDR, per-series false
positive rate, mixed-family descriptive FDR, sensitivity by scenario,
first-cycle sensitivity, detection delay, latency guards, and the primary
scenario's v2 comparison. This separation prevents the family-level FDR trial
count from being confused with the per-series false-positive denominator.

### Current calibration diagnosis

The null generator applies structural-zero replacement uniformly through the
endpoint window. An earlier harness version suppressed structural zeros in the
last eight periods, which made the recent low-count window systematically
higher than its history and therefore was not a valid complete null. Correcting
that simulation defect is a stricter test; no detector threshold was relaxed.

The checked run has three families with a review discovery among 80
complete-null families: 3.75%, with a 95% Wilson interval of 1.28%–10.45%.
The point estimate passes the nominal 5% criterion, but the confidence-bound
decision remains inconclusive and the overall calibration remains failed. One
family is `common_count` and two are `rare_count`; all use a converged primary
fit rather than the empirical fallback.

The rare-only correction removes one identified source of anti-conservatism:
the robustly trimmed process dispersion is no longer reused as a predictive
tail dispersion, and fitted-mean parameter uncertainty is kept on the same
untrimmed Pearson scale. The paired no-structural-zero control now has 3/292
rare-tier raw p values at or below 0.01 (1.027%; Wilson 95% interval
0.350%–2.977%), compatible with a super-uniform 1% null under the harness
criterion.

The structural-zero stress still fails. It has 9/330 rare-tier raw p values at
or below 0.01 (2.727%; 1.441%–5.101%), so the implementation is not described as
calibrated and rare-count signals are not eligible for unattended statistical
publication. A historical excess-zero ZINB convolution was evaluated and
rejected: it changed 9/330 to only 8/330, left the rare null-family count at
2/20, reduced review and guarded sensitivity by 0.625 and 0.312 percentage
points respectively, and increased the full harness runtime from about 160 to
216 seconds. The simpler predictive correction is retained; the unresolved
structural-zero mismatch remains explicit.

### Guarded automation candidate

The report also evaluates a separate fail-closed automation candidate. It
requires current data, a stable completed primary fit (empirical fallback is
excluded), a passed effect threshold, a syntactically valid HTTP(S) evidence
link, and `q <= 0.01`. This does not change the `q <= 0.05` review-signal gate.

The guarded gate produces zero candidates among 80 complete-null families, but
the Wilson upper 95% bound is still 4.58%, above its nominal 1% target. Common
sustained-2x sensitivity is 75.31%, down 10.94 percentage points from review
sensitivity; rare sustained-4x sensitivity is 79.38%, down 6.56 points. The
strict gate therefore lacks evidence for unattended public release.

Until both conditions pass, guarded candidates may automate queueing,
deduplication, evidence collection, and review-form preparation only. Public
release must remain fail-closed. A rare-count candidate should enter the human
queue only when independently attributable official evidence is present;
otherwise it remains an internal `watch`/blocked item. Fallback fits, delayed
data, missing evidence, and failed quality gates remain blocked from automatic
publication.

## Storage and publication

Migration `0009_situation_v3` adds normalized analysis runs, signal results,
event clusters/items, period reports/members, review decisions, and publication
pointers. The dedicated history database stores the immutable report and signal
archive.

Publication order is invariant:

1. stage the immutable analysis run and full ledger;
2. pass the report quality gate;
3. commit the report to the history database;
4. commit the normalized primary rows;
5. atomically move the publication pointer.

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
