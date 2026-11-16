# Nigeria NCDC archive integration

Reviewed 2026-11-15. Canonical scope `ncdc_surveillance`; ontology source
`SRC_NG_NCDC`; stored source label `Nigeria NCDC Surveillance`.

Official entry points:

- [Weekly Epidemiological Reports](https://ncdc.gov.ng/reports/weekly).
- [Disease situation reports](https://ncdc.gov.ng/diseases/sitreps).

The collector traverses every linked weekly route and every disease category,
not a latest-report or selected-disease subset. The archive currently advertises
430 weekly routes and twelve situation-report categories: Lassa fever,
meningitis, cholera, mpox, yellow fever, measles, rubella, COVID-19, avian
influenza, diphtheria, dengue and Ebola. An advertised link is not proof that
its PDF is available or that its numerical tables can be verified.

## Implementation and replay

- Crawler and explicit PDF contracts: `src/data/crawlers/ng.py`.
- V3-only updater: `src/data/processors/ng.py:NigeriaNCDCUpdater`.
- Durable collect/offline organize: `src/services/ingestion/countries/ng.py`.
- Resumable archive capture and review export: `scripts/collect_ng_ncdc.py`.

```sh
# Capture every published archive link. Explicit recovery reuses only files
# whose receipts, byte lengths and SHA-256 hashes agree; failed items retry.
venv/bin/python scripts/collect_ng_ncdc.py --download --resume --workers 4

# Pure offline parse, verified raw-artifact manifest and review CSV/audit.
venv/bin/python scripts/collect_ng_ncdc.py --organize

venv/bin/python -m pytest tests/unit/test_ng_ncdc.py -q
```

Raw originals and individual receipts are in
`data/raw/ng/ncdc_full_history/`. `catalogue_audit.json` records every attempted
listing, including stale routes and zero-file categories. `capture_manifest.json`
records every discovered PDF and download failure. `artifacts_manifest.json`
has the standard `kind`, `source_url`, `relative_path`, `sha256`, `byte_size`
and `metadata_` fields so a task can register the captured originals and then
call `organize` without another network request. Original retrieval times are
retained; recovery is not labelled a successful live refresh.

The CSV `data/current/ng/nigeria_ncdc_surveillance.csv` and accompanying
`source_audit.json` describe verified numerical extraction. Discovery, capture,
extraction, database import and public export are separate milestones. The
archive is explicitly **partial numerical coverage**, even when every readable
PDF has been archived. The audit enumerates unparsed reports and known categories
mentioned without a verified series; it does not replace them with zeroes.

## Numerical contracts

Every count retains the original disease label, source URL/hash, classification,
measure, reporting grain, period boundaries and source report period. Source
category identities distinguish WER from single-disease situation reports;
overlapping observations must never be added together.

- Modern WER: dedicated national disease tables supply suspected, confirmed
  and death counts. The source-native influenza series retains its sentinel
  scope. Clearly identified comparative-year rows remain cumulative counts.
  Summary cards and dedicated tables are not assumed to agree; this adapter
  uses the dedicated tables and does not parse the cards into duplicate facts.
- Legacy IDSR WER: the merged first-page summary requires exact disease,
  variable, five-column and epidemiological-year/week alignment. Previous-week,
  current-week, prior-year and YTD columns are distinct. Generic `Cases` retain
  `idsr_reported` classification; they are not silently relabelled confirmed.
- Legacy narrative WER: only explicit summary statements are extracted.
  An omitted confirmation or death count is unknown. AFP notifications are not
  converted into polio cases from a phrase about polio confirmation. Generic
  case counts retain `reported` classification unless the statement explicitly
  calls them suspected. Unicode bullets cannot extend a disease's count scope.
- Lassa: suspected, confirmed, probable and confirmed-case deaths remain
  separate in verified national current-week/comparative YTD tables, including
  reordered and compact-text templates. Older suspected(negative) tables retain
  the outer suspected count and exclude negative tests. Legacy reporting-week
  prose preserves explicit number words and cross-year outbreak boundaries.
- Meningitis: the verified current-week table and cumulative season preserve
  the explicit cross-year epidemiological-week-40 origin; it is not January YTD.
- Mpox: explicit current-week and YTD suspected, confirmed and death highlights,
  confirmed(suspected) paired tables and January-start key indicators. Multiweek
  current periods retain their full interval; prior-year week 52 is not week 40.
- Measles and dengue: national monthly classification tables. A multi-year
  outbreak total is not accepted as a single-year YTD observation.
- Diphtheria: current-week and explicitly year-labelled cumulative tables.
- Yellow fever: explicit monthly and YTD statements, retaining zero deaths
  only where directly stated. Presumptive-positive tests are not confirmations.
  Older weekly prose can supply independently verified national counts; explicit
  IP Dakar confirmations retain their narrower laboratory classification.
- Rubella: explicitly labelled total/new-in-month lab-confirmed case/death
  cards. Congenital-rubella sentinel periods and conflicting tested counts need
  separate contracts and remain in raw documents.
- Cholera: explicit national current-week suspected/death highlights and YTD
  statements. Nominal months covering epidemiological-week windows are not
  silently treated as calendar-month observations.
- COVID-19: verified national table totals retain cumulative cases, recoveries,
  deaths and tests separately. Explicit daily reports and published daily vectors
  are imported only after validating the vector width and its stated total.
  A failed daily-vector reconciliation withholds that vector and retains verified
  cumulative totals with a visible issue. Testing-column order is verified for
  older 8/9-column layouts; samples collected are not relabelled tests. Midnight
  report-date anchors prevent selecting the start of a cross-year report window.
  No incidence is derived by differencing cumulative snapshots.

Numerical extraction currently focuses on national observations. Source state,
LGA, sex/age, contact-tracing and image/graph tables remain in the complete raw
archive and are explicit remaining coverage work. Ebola reports containing
contact-monitoring counts are not relabelled as routine infection counts.
Avian influenza currently has an archive category without downloadable reports.

## Revisions, quality and release

The PDF's explicit reporting period wins over a mismatched navigation route,
and the discrepancy is retained. Weekly dates use the documented Monday–Sunday
epidemiological-week contract; monthly facts use the first day as their stored
month key and retain the actual month end separately. Source explicit zeroes
remain zero; missing markers remain unknown.

Newer comparative reports can revise earlier source observations. Same-period
conflicting observations are withheld and listed in the audit. Collection or
parse failures do not authorize deleting existing observations; the updater
performs additive/revisable v3 writes, not authoritative window withdrawal.
Unreviewed categories are retained for Mapping Registry v3 review rather than
coerced into existing disease IDs.

Public release defaults to disabled while reuse terms and generated snapshots
remain unverified. A local CSV or stored source-native holding series must not
be described as a published or comprehensive Nigerian national surveillance feed.

## Database acceptance snapshot

Read-only verification after task `d1f3e346-cc66-4705-89bd-bb94cb8120a9`
completed at **2026-11-15 15:42:42 UTC** found the following committed state.
Evidence is recorded in `data/current/ng/database_acceptance.json`; the import
log is `data/current/ng/database_import_final_frozen.log`.

| Milestone | Verified count |
| --- | ---: |
| Discovered PDFs | 1,690 |
| Captured PDFs | 1,645 |
| Reports with verified numerical extraction | 1,225 |
| Captured reports without verified numerical extraction | 420 |
| Organized CSV observations | 15,128 |
| Committed database holding facts | 15,128 |
| Committed numeric values | 15,128 |
| Holding series | 177 |
| Explicit source zeroes | 4,138 |
| Same-period conflicts withheld during organization | 64 |

The CSV and database counts are independently checked milestones; in this
snapshot they agree, including counts grouped by period, measure, reporting
basis and frequency. A captured report with partial national extraction is
counted as parsed even when its state, age/sex or graph data remains raw.
The 420 unparsed reports and 45 unavailable downloads are not zero-valued facts.

All committed facts belong to `SRC_NG_NCDC`, country `NG`, geography
`country:NG:national`, with the exact original source label
`Nigeria NCDC Surveillance`. Their dates span **2015-09-06 to 2026-11-01**.
All 177 series remain unmapped, non-additive and not comparable; no legacy
`disease_records` projection or public release was created. The 15,128 facts
include 9,858 case observations, 4,613 death observations, 564 recovery
observations and 93 testing observations. These are separate source facts,
not a count of people or a national case total.

Database checks found zero null/unknown values, negative values, fractional
counts, non-finite values, source/value/date/definition mismatches, or cumulative
versus incident dimension mismatches. All weekly dates retain Sunday endpoints;
monthly dates retain first-of-month keys. Explicit multiweek intervals remain
multiweek. Checks against original report examples retained Lassa 2020 week 3
suspected cases 159, COVID cumulative tests 975,786 on 2021-01-03, and Mpox
suspected cases 102 over weeks 25–28 of 2024.

The quality guard stayed `fail_closed`. It reported two warnings,
`incoming_batch_period_gap` and `projected_series_time_gap`, reflecting sparse
source-series timelines. These warnings were retained; no missing period was
filled with zero and no guard was relaxed. Numeric completeness for the wider
archive remains partial.
