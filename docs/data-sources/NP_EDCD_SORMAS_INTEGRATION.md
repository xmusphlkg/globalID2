# Nepal EDCD public SORMAS weekly integration

Verified on 17 November 2026. Source scope: `np_sormas_weekly`; source identity:
`SRC_NP_EDCD_SORMAS`; updater: `NepalSORMASUpdater`.

The official [public dashboard](https://analysis.edcd.gov.np/) loads these
unauthenticated, aggregate endpoints directly from its published JavaScript:

- `GET /sormas-public-rest/public/v1/bulletin/weeks`
- `GET /sormas-public-rest/public/v1/bulletin/{year}/{week}`

No login, private SORMAS API, database access, or credential extraction is used.
Public accessibility does not establish redistribution rights. Reuse terms,
clinical category mappings and calendar boundaries remain unverified, and
public release and default scheduling remain disabled.

## Live acceptance

The collector captured all 15 listed 2026 reports: weeks 13, 18–24, 26–29, 31,
35 and 45. Offline organization produced 1,080 observations: nine source
categories × eight geographies × 15 source weeks. The eight geographies are
the published Nepal aggregate and seven provinces, stored independently.
This does not establish complete reporting-network or calendar-year coverage.

[Probe receipt](evidence/np-sormas-probe-20261117.json) records the raw manifest,
byte counts, report-level audit and explicit database/publication status.
The 17 local raw artifacts (14,450,194 bytes) were replayed through the usual
`ArtifactWriter`, organization and source-series store. The internal ledger now
contains 1,080 rows; the import receipt is
`data/current/np/sormas_historical_import_20261117.json`.

The real week 19 and week 45 responses and week index are preserved in
`tests/fixtures/ingestion/NP/`, with checksums in `provenance.json`.

## Observation contract

Only `items[].selectedWeekCount` and corresponding
`distributionRows[].provinceCounts` are extracted. The parser reconciles the
selected count against the matching trend point, distribution total and a
complete set of province counts. It never sums the national aggregate and its
provinces. Missing province cells remain unknown; literal zero remains zero.
Duplicate source categories, identity mismatches, negative/noninteger counts
and conflicting totals fail the report. Partial runs retain an explicit audit
and never authorize withdrawal of an entire stored window.

`items[].totalCount` is a trend-window total. `yearlyTotalCount`, narrative
numbers, event signals, district/community breakdowns, deaths and laboratory
classification are not imported by this initial contract. All remain available
in the original aggregate response for subsequent review.

Syndromes and diseases have distinct source codes and dimensions. SORMAS
clinical/reporting definitions and network completeness must not be borrowed
from the separate EWARS system. `ReportingBasis=source_unspecified` preserves
that uncertainty; unreviewed categories enter internal holding series.

## Source weeks and revisions

`periodStart` describes a multiweek trend window, rather than the selected
week's start. For example, week 45 spans a reported window beginning in
September. Week 35's `periodEnd` falls midweek. These fields therefore remain
raw provenance and never define inferred weekly intervals.

Rows retain source year/week in stable `Dimensions` and use January 1 solely
as a labelled storage anchor (`DateRole=source_year_storage_anchor_only`).
They must not be displayed as January observations or used in public weekly
comparisons until the calendar contract is reviewed. Distinct weeks sharing
the anchor retain distinct storage identities; the real holding-store test
also verifies that national and provincial facts remain separate.

Publication timestamps and trend-window dates remain outside identity
dimensions so changes to them cannot create duplicate revised observations.
Individual source observations permit authoritative revisions; missing weeks
are never manufactured or treated as zero.

## EWARS research

The independent [EWARS platform](https://ewars.edcd.gov.np/webbulletin/) exposes
the public HTML path `data/{year}SunW{week}-ewars-bulletin.html`. Week 19 of
2025 returned a real bulletin with six comparison-table categories and a
134-site reporting statement. Probed 2026 weeks 19 and 42 returned 404.
The EWARS page is evidence of a separate potential source, not an API for
SORMAS. It has not been merged into this collector or represented as complete
2026 weekly coverage.

## Operation

```bash
venv/bin/python main.py crawl --country NP --source np_sormas_weekly --process --save-raw
venv/bin/python -m pytest tests/unit/test_np_sormas.py tests/unit/test_south_asia_onboarding.py -q
```

The crawl command enqueues a task; the historical replay receipt records task
completion and stored facts. Initial collection still requires the four new
source identities to be synchronized through the normal reviewed ontology
workflow before public use. Offline replay consumes immutable artifacts and
performs no source requests. The default maximum is 200 reports; an oversized
window fails explicitly and requires selecting narrower years.
