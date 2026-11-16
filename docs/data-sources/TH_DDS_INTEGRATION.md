# Thailand Report 506 and DDS integration review

Reviewed on **2026-11-15**. Thailand now has an internal, source-native historical
monthly adapter. Public release remains disabled: reuse rights and projection
mappings are unverified. A successful internal parse is not a public country
snapshot or a claim of comprehensive national infection counts.

## Implemented source

- Official catalog: <http://doe1.moph.go.th/surdata/index.php>.
- Scope: `th_doe506_monthly`; ontology source: `SRC_TH_DOE506`.
- Transport: `src/data/crawlers/th.py`.
- Durable capture and offline organization:
  `src/services/ingestion/countries/th.py`.
- Updater: `ThailandDOE506MonthlyUpdater` in `src/data/processors/th.py`.
- Default refresh: discover actual monthly history links and select the latest
  **three advertised closed years**, bounded to 216 disease/year reports. New
  source releases and corrections are rediscovered on each refresh; the window
  is not fixed to 2023. Advertised links are not proof of complete source data.
- History mode: `backfill_history=True` (or `mode="history"`) discovers all
  linked history from **2003** onward. `report_offset`, `max_reports`,
  `next_report_offset` and `remaining_reports` bound resumable batches. The
  recorded `plan_hash` must match `resume_plan_hash` on continuation; a changed
  inventory requires a new plan. Advance the cursor only after successful store.

The official 506 catalog explains that reporting uses provincial health offices,
hospitals and health stations, covers public hospitals but not all private
hospitals, and mostly uses suspected rather than confirmed case definitions.
Retain these as **reported notifications**, with incomplete private-sector
coverage. The source's month definition is retained as `source_report_month`;
it is not silently assigned the DDS onset-day or reporting-day definition.

The catalog contains **72 source categories**: 67 individual categories and five
aggregates (dengue, measles, encephalitis, tuberculosis and STI). This includes
official surveillance categories such as poisoning as well as infectious
diseases. Every category is fetched; none is automatically mapped to a disease
concept because of its English name. Aggregate and component source codes
remain separate and must never be summed into one curve.

The completed historical review downloaded every available catalog link for
**2003–2023** and independently validated **1,324 disease/year reports**, yielding
**15,888 disease/month rows**, from 2003-01 through 2023-12. Missing official
reports remain gaps, principally HTTP 404 responses; they are never filled with
zero. Valid report counts per year are:

| Year | Reports | Year | Reports | Year | Reports |
| --- | ---: | --- | ---: | --- | ---: |
| 2003 | 53 | 2010 | 61 | 2017 | 65 |
| 2004 | 54 | 2011 | 61 | 2018 | 66 |
| 2005 | 54 | 2012 | 61 | 2019 | 71 |
| 2006 | 56 | 2013 | 64 | 2020 | 72 |
| 2007 | 59 | 2014 | 64 | 2021 | 72 |
| 2008 | 60 | 2015 | 64 | 2022 | 72 |
| 2009 | 60 | 2016 | 63 | 2023 | 72 |

All **72/72 2023 reports** validate, producing **864 rows**. Their source footer
date is **2024-09-12**. Reproducible representative fixtures and per-report source
evidence live in `tests/fixtures/ingestion/TH/`. There is deliberately no sum
across diseases because source categories overlap.

## Validation and revision contract

1. Discover the disease from the official index and its monthly RTF from the
   official history page. Do not construct guessed report filenames. Unexpected
   category codes or changed catalog labels require contract review.
2. Require the source's Windows-874 RTF signature, national geography, exact
   Buddhist/Gregorian report year, month order and case/death metric order.
3. Require exactly one national `Total` row. Province and zone rows are not
   added to that row. Grouped report headers have their own exact contracts.
4. Require the source footer's update date to be after the reporting year
   closes. Calendar age alone does not make a source file complete.
5. Validate nonnegative integer counts and reconcile the 12 month values with
   the published annual total separately for cases and deaths where all values
   are present. Do not round, repair, clamp or silently infer counts.
6. Preserve explicit zeros. Blank, dash and asterisk counts stay unknown, with
   `MissingValueStatus=source_missing` for missing case counts. Death counts
   remain in their separate source-row field; they are never added to cases.
7. Preserve source code, native report label, URL, raw hash, source update date,
   definition version and authoritative revision metadata. Refetching may
   revise validated observations. There is no country-wide authoritative window
   deletion and no fallback to a cached file masquerading as a live refresh.
8. Validate reports independently. Failures appear under `source_audit.failed_reports`
   and mark the run `partial`; independently valid reports remain reviewable.
   An incremental run with no validated reports fails. A history batch containing
   only audited unavailable reports may complete empty so its cursor can advance.
   Raw persistence failures propagate instead of becoming harmless source gaps.
9. Organize reads captured artifacts only. It verifies each report against its
   captured catalog/history link and performs no network access.

The live default and offline fixtures agree on examples: influenza has 486,973
notifications in 2023; the separate dengue aggregate has 160,839. Anthrax,
avian influenza, botulism, poliomyelitis and neonatal tetanus have explicit
zero rows rather than missing months. These examples do not imply complete
population coverage or verified diagnosis.

## Rejected 2024 snapshots

[Dengue 2024](http://doe1.moph.go.th/surdata/y67/tmcd_DHF_Total_67.rtf) and
[influenza 2024](http://doe1.moph.go.th/surdata/y67/mcd_Influenza_67.rtf)
have source footer date **2024-12-25** and cannot establish a complete 2024
calendar year. The influenza national monthly values sum to **538,985**, while
the annual total cell says **650,931**. The adapter rejects both preclosure
reports; it never repairs the influenza discrepancy or publishes these as
complete annual history. Advertised 2025 links also returned 404 during review.

## Current DDS sources remain separate

The official [DDS portal](https://ddsdoe.ddc.moph.go.th/dds-portal/) now links
to [DOE DDS-Report](https://doe.moph.go.th/dds-report/). Its national report
form and download were publicly accessible without signing in during this
review, so the older blanket claim that all national exports require login
is no longer accurate. The published form offers years 2022–2026 and explicitly
separates `onset` (onset date/place) from `gen` (report date/place).

A normal session and the public form's CSRF token successfully downloaded the
2025 national priority report as a PDF. Its final table contains **20 ranked
disease categories**, not a full national disease inventory. Full-year choices
such as `1-53` and current-year windows such as `1-47` are cumulative intervals,
not single weekly observations. No production DDS adapter is enabled from this
limited report. Do not difference cumulative snapshots into weekly incident
counts: revisions and case definitions need their own explicit contract.

The public Tableau export for
`DDS_Public_Weekly_Disease_Priority/sheet3.csv` returned only the province
selector column and “all provinces,” not a usable national case table.
The DDS no-case reporting service still redirects to login. No credentials,
login bypass or private export was used.

The government catalog entry `gdpublish-ddce-01` describes **event-based
vector-borne outbreak surveillance**, including cluster verification and media
signals. It must not become a national notification feed. Region-6 COVID
catalog entries also cannot be relabeled as Thailand national totals.

## Verification

```bash
venv/bin/python -m pytest tests/unit/test_th_doe506.py -q
```

The suite parses every one of the 72 real 2023 reports offline and covers legacy
2003/2019/2020 report layouts, missing
counts, explicit zeros, overlapping categories, month/metric order, source
calendar, wrong geography/code, preclosure files, annual-total mismatches,
partial network/parse failures, dynamic history discovery, cursor/hash contracts,
duplicate artifacts and tampered import bundle members.

The local review export is `data/current/th/thailand_doe506_monthly.csv`, with
`data/current/th/source_audit.json` retaining per-report source evidence.
`scripts/import_thailand_history.py` replays the original downloaded source
bytes through the normal staged ingestion and source-series transaction. It
creates audited tasks, CrawlRuns and collect/organize/store stages. Each yearly
raw ZIP is a durable SourceArtifact with a manifest retaining every original
URL, byte length, SHA256 and metadata; outer archive, manifest and member hashes
are verified before parsing. Import does not fetch from the network or use the
review CSV as source data. Task and disease mapping notifications are disabled.
Observations stay in source-native holdings, and public release stays disabled.

The production database import completed on 2026-11-15: **15,888 observations**
in **72 source series**, spanning 2003-01-01 through 2023-12-01. All **21 yearly
tasks and CrawlRuns** completed collect, organize and store. The 2023 run is
`29308` (task `730eaafc-405e-4bdc-826d-b1daaca6dc19`); the 2003–2022 result
ledger is `data/current/th/import_results_2003_2022.json`. A preliminary 2022
capture was deliberately interrupted to switch to manifest bundles; its task
and collect stage remain failed with organize/store skipped and no observations
stored. This audit is retained rather than hidden or relabeled as successful.
