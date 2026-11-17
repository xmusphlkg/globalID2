# Bangladesh DGHS dengue and IEDCR surveillance

## Implemented: DGHS daily hospital surveillance

Scope `bd_dghs_dengue_daily`, source `SRC_BD_DGHS_DENGUE`, updater
`BangladeshDGHSDengueUpdater` (`src/data/processors/bd_dengue.py`).

The [official press-release directory](https://dghs.gov.bd/pages/miscellaneous-infos?filters=%7B%22miscellaneous_info_type%22:%226a9cf0471fa8cd87d1f50227%22%7D)
links daily PDFs published through Bangladesh's government object storage.
Verified 2026-11-17: the current directory has 72 publications from 2026-09-04
through 2026-11-15; `page_size=100` is a public server-rendered directory option.
The supplied `https://dashboard.dghs.gov.bd/pages/heoc_dengue_v1` returned 404.
No unverified dashboard API is claimed.

The newer first-page table states daily hospital admissions and deaths, cumulative
admissions and deaths from 1 January, discharges, and current inpatients for all
eight divisions and the national total. Four metric series are retained for
each geography; discharges and current inpatients validate the cumulative
balance. Hospital admissions are **not population incidence**. The daily
interval is the source's last-24-hours reporting label; no midnight or onset
timestamp is invented. Cumulative values are not differenced or summed with
daily values. City corporations, hospitals and districts on subsequent pages,
age/sex charts, and historical annual/monthly summaries are outside this
numerical table contract (`bd_dghs_dengue_division_hospital_daily_v1`).

Earlier reports use an English dashboard PDF. Its first page has explicit
last-24-hours national admission/death labels, parsed separately as
`bd_dghs_dengue_dashboard_explicit_daily_v1`. When all ten division/city
corporation value labels and their ordered category labels appear, admissions
are retained and checked against the source-stated national total. DNCC, DSCC
and Dhaka Division outside city corporations remain separate source reporting
regions; they are not mislabelled as a complete Dhaka division. If the chart
omits categories, only the two explicit national observations are retained.
In particular, omitted categories never become inferred zero counts or missing
region sums. Regional daily deaths, cumulative dashboard headlines and
age/sex charts remain outside this dashboard contract.

The real 2026-11-15 fixture has 1,952 daily admissions, 8 daily deaths, 162,509
cumulative admissions and 499 cumulative deaths nationally. Exactly 36
observations cover four metrics across nine geographies. The table parser checks the
source date, eight division identities/order, national sums across all six
columns, and cumulative admissions = deaths + discharges + inpatients. Changed
or incomplete tables fail closed. A real 2026-10-10 dashboard PDF yields 1,631
national admissions, 4 deaths and ten explicit regional admission counts (12
observations). The 2026-09-04 dashboard has national 512 admissions and zero
deaths; incomplete regional categories are not imported.

The official 2026-11-02 report is catalogued with publication date 2026-11-03;
the observation date comes from the title cross-checked with the PDF, while
the directory publication date is retained separately. Some official directory
titles omit their closing parenthesis; their complete day/month/year still
has to match the PDF. There is no 2026-09-18 entry in the captured directory;
the missing report is not synthesized.

Collection captures directory pages, linked original PDFs, retrieval times,
hashes and a manifest before parsing. Organization replays those immutable
artifacts without network access, validates source linkage and records
individual download/parse failures. Default collection is bounded to 100
reports and 10 directory pages, configurable up to 1,000 reports/30 pages.
The older archive is not represented as complete coverage. Missing days remain
unknown. Source reuse review and the verified country release remain pending;
automatic mapping emails and public release are disabled.

## Historical archive probe, 2026-11-17

The official [DGHS dengue press-release static page](https://dghs.gov.bd/pages/static-pages/dengue-press-release-gxrgtg-6a9eb4cc7a024513d1b8c896)
contains 1,878 historical PDF links: 246 from 2026 (including
2026-09-03 and earlier), 365 from 2025, 360 from 2024, 365 from 2023, 365
from 2022, 19 from 2021 and 158 from 2019. This is a valuable discovery
index, but its originals are hosted on `dashboard.dghs.gov.bd`, separate from
the current government object-storage PDFs.

Representative original retrievals for 2026-09-03, 2025-06-01 and 2019-12-01
returned HTTP 502 `text/html` (150 bytes), not PDF bytes, on 2026-11-17.
Therefore no historical number was admitted, and no missing day was set to
zero or interpolated. The original 866,933-byte static index and its immutable
manifest are retained at `data/raw/bd/history-index-20261117/`; probe hashes
and year counts are in
[`evidence/bd-dghs-history-20261117.json`](evidence/bd-dghs-history-20261117.json).
When the dashboard host serves valid originals, these URLs can be added as a
separate historical capture and replayed through the same fail-closed parser.

```bash
venv/bin/python -m pytest tests/unit/test_bd_dengue.py -q
python main.py crawl --country BD --source bd_dghs_dengue_daily
```

## Verified capture and replay, 2026-11-17

All 72 PDFs in the current directory were captured and replayed successfully:
21 Bengali division summary reports and 51 English dashboards, yielding 1,268
source observations from 2026-09-04 through 2026-11-15. There were no download
or parse failures. The archive contains 74 artifacts (directory, PDFs and
collection plan), 76,372,685 original bytes. Manifest:
`data/raw/bd/20261117T013609Z-60d7bb44/manifest.json`; CSV and detailed audit:
`data/current/bd/20261117T013609Z-60d7bb44/`. This is complete replay of the
captured directory, not a claim that all historical dates or regions exist.
The tracked evidence is
[`evidence/bd-dghs-dengue-20261117.json`](evidence/bd-dghs-dengue-20261117.json).

The archived 2026-09-29/30 dashboard national labels contain duplicated comma
glyphs (`1,,942` / `1,,948`). A narrow extraction repair is allowed only when
the explicit admission label, all ten regional counts and the explicit
age/sex grand total agree on the same integer. Both the original label and
`ExtractionRepair` remain on observations and the audit identifies both dates.
No ambiguous digits, missing/suppressed values or unverified totals are repaired.

```bash
venv/bin/python scripts/imports/import_surveillance_archive.py --country BD \
  --manifest data/raw/bd/20261117T013609Z-60d7bb44/manifest.json
```

The command above previews the archived observations without database writes.
Use the normal staged ingestion runner for storage and mapping discovery.

## Separate project: IEDCR NISB ILI/SARI

The [supplied NISB page](https://www.iedcr.gov.bd/site/page/81690739-f87b-49b6-bba6-0eb7fe6b7536/nolink/)
and `https://iedcr.gov.bd/` / `https://iedcr.portal.gov.bd/` failed verified TLS
requests in this environment on 2026-11-17. TLS validation is retained. No
numeric ILI/SARI feed, lab denominator, sentinel-site roster or open API was
verified, so IEDCR is not silently represented by DGHS dengue or WHO FluNet.
It remains an independently tracked follow-up source; WHO laboratory
cross-checks would require their own scope and measure definitions.
