# Bhutan RCDC publications and sentinel surveillance

Validated on 2026-11-17 against the official [RCDC homepage](https://www.rcdc.gov.bt/web/), [latest respiratory publication](https://www.rcdc.gov.bt/web/?p=8511), and its [original PDF](https://www.rcdc.gov.bt/web/wp-content/uploads/2026/11/Wk-4344.pdf). The bounded revalidated archive contains 100 selected publication records, 48 parsed respiratory reports, and 96 verified weekly observations; 37 publications remain document-only and 15 older layouts remain explicitly unparsed. The validated observations were replayed through the staged runner into holding series; public release remains disabled. Receipts, original-byte hashes, discovery counts, and observations are in [the evidence file](evidence/bt-rcdc-20261117.json).

## Contract

| Field | Value |
| --- | --- |
| Country | BT / Bhutan / 不丹 |
| Scope | `bt_rcdc_publications` |
| Source system | `SRC_BT_RCDC_PUBLICATIONS` |
| Updater | `src.data.processors.bt.BhutanRCDCPublicationsUpdater` |
| Connector | `src.services.ingestion.countries.bt` |
| Source label | Bhutan RCDC Publications and Sentinel Surveillance |
| Discovery | Official HTML articles, publication date, title, and post ID |
| Publication types | Respiratory surveillance, monthly epidemiology, quarterly bulletin, annual scientific report, outbreak/event report |
| Numeric observations | Explicit single-week influenza laboratory detections from supported respiratory summaries |
| Geography | Bhutan reporting sentinel network (`country:BT:national`) |
| Start year | 2015 (discovery; does not certify numerical coverage since 2015) |

The website's REST-looking `/web/wp-json/wp/v2/posts` URL returned homepage HTML during investigation. The connector therefore discovers official HTML links and does not assume an open JSON API.

## Verified values and limits

The November 7 publication covers epidemiological weeks 43 and 44, October 19–November 1, 2026. The PDF summary explicitly reports influenza positivity of **44/102 in week 43** and **13/41 in week 44**. The observations are 44 and 13 `laboratory_detections`, dated October 25 and November 1. The denominators and published percentages remain attached as provenance. The reporting population is a sentinel network; these values do not estimate all influenza cases in Bhutan.

Both source date boundaries and the week pair must agree with the supported weekly calendar. Counts must agree with the published positivity percentages. Conflicting duplicate observations are excluded. Missing weeks are unknown.

The same PDF reports RSV and SARS-CoV-2 totals across the two-week testing interval. Those totals are retained in the original document and are not divided into weekly values. Embedded image tables and curves are not digitized. Monthly, quarterly, annual, and outbreak documents are captured as publications; their narratives are not converted into national weekly case counts. For example, the April 2026 monthly report contains a total across notifiable disease categories and percentages; rounded chart percentages are not multiplied to invent exact disease counts.

The current source-series store supports daily, weekly, monthly, quarterly, and annual observations. The publication's biweekly cadence is recorded separately from the weekly observations it explicitly reports.

## Collection, replay, and backfill

Collection stores the official listing, selected publication pages, linked original PDFs, and a discovery plan before organization. Replay verifies the listing hash, discovered/selected posts, publication-to-attachment links, and document hashes without network access. Every selected document is classified as parsed, document-only, unparsed, or failed download in the source audit. Event publication metadata remains available under `source_audit.events`.

The homepage also has a separate **Event Reports(Latest)** table. Its five rows in the validated snapshot are retained under `source_audit.latest_event_reports`, with source date, Dzongkhag, event name, reported event count, and status. They include a November 7 hand-foot-and-mouth report in Trongsa and a November 2 suspected flu outbreak in Lhuentse. These counts describe event snapshots, so they are not added to disease-series observations. Detailed residential-location cells are not copied into structured audit metadata.

Default `max_reports=24` bounds a routine refresh. The audit records the full discovery list and how many publications were deferred. Use a larger `max_reports` (up to 2000) and explicit `start_year`/`end_year` for historical capture. A discovery start year or a captured document count is never a completeness claim. An archive containing only document publications may correctly yield zero numerical observations.

The collector closes its HTTP session after each run. Source mapping notifications are disabled. The native source-series store handles writes; the legacy import hook intentionally writes no projected records.

## Validation

`venv/bin/python -m pytest tests/unit/test_bt_rcdc.py -q` — 11 tests passed. Tests use an original official PDF and small HTML excerpts captured from official pages, verify numeric/calendar semantics, retain event counts separately from incidence, exclude unrelated and external-host PDF links, exercise staged collection/replay, and reject a tampered listing receipt.

The original revalidated run used `max_reports=100` and captured a bounded subset containing **96 verified weekly influenza observations**, which were replayed into holding series. A later immutable News and Publications listing snapshot confirmed 598 dated publications from 2015–2026. It is stored at `data/raw/bt/20261117T-full-20261117-priority/manifest.json` with its audit in `offline_audit.json`. The listing contains the complete discovered catalogue, but RCDC throttled all 49 priority 2025–2026 respiratory detail requests in the follow-up run; those are recorded as `document_only`/failed and produced zero new observations. No partial download directory is imported. The catalogue snapshot adds historical publication coverage, while the 96 verified observations remain the numerical values claimed in this validation.
