# Afghanistan WHO weekly outbreak situation reports

This is an independent, numerically verified source alongside the inaccessible
MoPH NDSR collector. It does not certify MoPH report coverage or full national
incidence. The verified [WHO archive](https://www.emro.who.int/afg/information-resources/infectious-disease-outbreak-situation-reports.html)
contains reports for weeks 1–50 of 2025 as observed on 2026-11-17. It also links
older reports back to 2022, outside the current default 2025 search window. The
observed archive does not establish 2026 numerical coverage.

The historical replay captured 48 unique PDFs in 2022, 49 in 2023 and 51 in
2024. One 2022 URL returned a non-PDF response and is retained as an explicit
download failure. The 2023 index links the same `WEEK_18_2023.pdf` URL under
both week 19 and week 18; the collector retains the first link and records the
conflict. The PDF cover remains authoritative, so a mismatching period is
rejected rather than relabelled.

| Contract | Value |
| --- | --- |
| Country/scope | `AF / af_who_outbreaks_weekly` |
| Source ID | `SRC_AF_WHO_OUTBREAKS` |
| Crawler | `src.data.crawlers.af_who_outbreaks` |
| Updater | `src.data.processors.af_who_outbreaks.AfghanistanWHOOutbreaksUpdater` |
| Staged connector | `src.services.ingestion.countries.af_who_outbreaks` |
| Geography | `source:AF:who_outbreaks:reporting_sites` |
| Numerical release | Holding series pending reviewed registry mappings/public snapshot |

## Verified originals and extracted values

Three genuine originals and the official index are retained in
`tests/fixtures/ingestion/AF`, with SHA-256 provenance:

| Report | Explicit source interval | Verified regression examples |
| --- | --- | --- |
| 2025 W01 | 2024-12-29–2025-01-04 | ARI-Pneumonia 38,012 suspected cases/99 suspected deaths; measles 1,136/8; dengue 9/0 |
| 2025 W49 | 2025-11-30–2025-12-06 | ARI-Pneumonia 38,819/94; measles 409/3; COVID-19 43 confirmed cases/0 reported deaths |
| 2025 W50 | 2025-12-07–2025-12-13 | ARI-Pneumonia 37,388/65; measles 405/3 |

W01 has 14 readable current-week case/death observations across seven source
categories. W49 yields six observations, and W50 yields four. The other W49/W50
matrices are rotated or text-extract incorrectly; they are audited as unreadable,
not guessed from a chart or narrative. The W50 report identifies 607 reporting
sites out of 613 sentinel sites, so the series retains a reporting-site geography
rather than a complete-national-incidence claim.

The historical replay produced 333 observations: 0 from 2022, 22 from 2023 and
311 from 2024. The 2022 layout uses current-week narrative blocks and no
supported exact matrix, so all 47 captured PDFs remain unparsed. The 2023 and
2024 audits retain incompatible or rotated layouts; only exact current-week
matrix cells enter the historical CSV. This is a coverage boundary, not an
assumption that the source reported zero cases.

## Extraction and refresh boundaries

Discovery follows genuine links from the official archive. Both the linked
report year/week and the cover identity must agree. The reporting interval comes
from the cover, including cross-year intervals; no ISO calendar is inferred.
Only the explicitly identified current-week column in a named disease matrix is
read. Historical columns remain excluded because the cover does not explicitly
date each previous week. Published index date typos do not override a verified
PDF's explicit date range.

Suspected, confirmed and unspecified source case definitions retain distinct
source category identities. Cases and deaths use independent `Measure` values;
the shared source-series payload stores both in its numeric `Cases` column.
Laboratory sample counts, positivity, cumulative totals, response activities,
charts and province/district breakdowns never become case observations. Missing
cells remain unknown, and conflicting duplicates are quarantined.

The defaults capture the latest 12 linked reports. Set `max_reports=260` to scan
the entire current 2025 window, or pass `weeks=[1,49,50]` to explicitly select
source weeks. Downloads use at most four independent HTTP clients by default.
Originals use an integrity-checked cache and are copied into immutable replay
artifacts with URLs, hashes, pages and report metadata. Offline organization
revalidates every report against the captured index and collection plan.

Historical corrections mentioned in subsequent reports are a separate gap:
for example, W50 states that W49 COVID-19 cases were revised from 43 to 42.
This collector preserves W49's own current-week value and does not reinterpret
historical revision notes or silently subtract cumulative counts. The extracted
observations therefore remain provisional and are not a fully reconciled
historical revision series.

`requires_verified_observations=True` makes a processed run fail if no numerical
observation survives, while preserving the organize audit. A collect-only run
can retain originals for diagnostics. Public release is disabled pending verified
category mappings and snapshot review; the separate MoPH scope remains subject
to its own source-access audit.

## Replay and tests

The real full-window collection manifest is
`data/raw/af/who-outbreaks-20261117/manifest.json`. Offline output is
`data/current/af/afghanistan_who_outbreaks_weekly.csv`, with collection/extraction
details in `docs/data-sources/evidence/af-who-outbreaks-20261117.json`. The
2025 window replayed on 2026-11-17 contains 497 exact current-week observations
across 50 reports and 14 source category identities; it has no unparsed report.

The historical manifest is
`data/raw/af/who-outbreaks-history-20261117/manifest.json`; its offline CSV is
`data/current/af/afghanistan_who_outbreaks_weekly_2022_2024.csv`, with audit in
`docs/data-sources/evidence/af-who-outbreaks-history-2022-2024-20261117.json`.
It contains 333 exact matrix observations from 147 captured PDFs, one failed
download and one conflicting official link.

Run `venv/bin/python -m pytest -q tests/unit/test_af_ndsr.py tests/unit/test_af_who_outbreaks.py`.
The tests cover genuine PDF values, source provenance, date boundaries,
case/death identities, table gaps, missing cells, contradictory week columns,
archive integrity and offline plan validation.
