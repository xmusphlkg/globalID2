# WHO WPRO dengue: Lao PDR and Viet Nam

Verified 2026-11-16. Scope `who_wpro_dengue`, source IDs
`SRC_LA_WHO_WPRO_DENGUE` and `SRC_VN_WHO_WPRO_DENGUE`. This source is separate
from the WHO GHO annual indicators. It captures WHO distribution of national
surveillance reports; the source agency is retained per row.

Official discovery starts at [WHO WPRO dengue surveillance](https://www.who.int/westernpacific/wpro-emergencies/surveillance/dengue).
Each publication page must link exactly one PDF under WHO's reviewed CDN
path. The current listing contains six reports dated 2026-08-20 through
2026-10-29. Collection saves the HTML listing, all six publication pages,
all six PDF originals, and a frozen collection plan (14 artifacts per country).

## Numerical contract

The parser uses source-native PDF text, without OCR or chart digitization.
A country section ends at its chart caption, report-page footer or next
hemisphere heading; another country's paragraph cannot supply its dates.
A seven-day date interval must be stated explicitly. Source epidemiological
week numbers are retained without assuming ISO week boundaries. Previous-week
or comparison-year values never become current-week observations. Country
case definitions and originating agencies remain distinct.

- LA: six explicit weekly case intervals, six explicitly reported weekly death
  values (including source zeroes), and six cumulative snapshots: **18 rows**.
- VN: two verified weekly intervals, one explicit weekly death zero, and four
  cumulative case/death snapshots: **7 rows**.
- VN's two reports marked "no update" contribute no new observations. One
  report lacks explicit calendar bounds and one lacks the reviewed national
  agency attribution; both are reported as numerical extraction gaps.
- PH has no current numeric country section in this listing and is not enabled
  under this contract. The annex's Philippine case-definition entry cannot be
  interpreted as current surveillance data.

Weekly rows use the source-stated interval end. Cumulative snapshots use the
report's publication date, with `SourceDateBasis=publication_dated_cumulative_snapshot`
and `SourceCumulativeCutoffVerified=false`: a paragraph's cumulative count does
not establish an exact cutoff date. No cumulative differences are calculated.
Cases, deaths, weekly observations and cumulative snapshots have separate
source category identities and remain non-additive.

January publications can report the preceding December week. Explicit dates
retain their actual observation year; seven-day intervals spanning December
and January are supported without converting source epidemiological weeks to
ISO weeks. The parser and default observation window permit dates from 2015,
which is a validation floor rather than a claim of complete historical coverage.

The latest parsed original, [29 October 2026 PDF](https://cdn.who.int/media/docs/default-source/wpro---documents/emergency/surveillance/dengue/dengue_20261029.pdf?sfvrsn=fe85de23_1&download=true),
reports LA 232 cases for 12–18 October, one reporting-period death and 6,133
cumulative cases; VN 5,390 cases for 19–25 October and cumulative totals of
166,507 cases and 21 deaths. Those cumulative counts remain publication-dated
snapshots. The PDF notes that Lao and Vietnamese data are supplied by WHO
country offices and that national surveillance methods differ.

## Refresh, replay and remaining history

Every refresh rediscovers currently linked reports, refetches their originals,
validates a frozen plan and upserts observations. Revisions to an existing
interval supersede older report values; conflicting same-publication values
are excluded and audited. Download and parse failures remain explicit. Missing
weeks and omitted countries never become zeroes. No source withdrawal is
inferred from an incomplete current listing.

This is **partial historical coverage**: the older IRIS archive and graph-only
points are outside this reviewed contract. `coverage_complete=false` is always
retained. Public release remains disabled pending source terms, disease mapping
and snapshot validation. PDFium text extraction is serialized within this
connector because its native library is not thread safe.

```sh
venv/bin/python scripts/collect_wpro_dengue.py
venv/bin/python -m pytest tests/unit/test_wpro_dengue.py tests/unit/test_wpro_dengue_acceptance.py -q
```

Local manifests are below `data/raw/<cc>/wpro_dengue_current/<capture>/`.
`data/current/<cc>/wpro_dengue_source_audit.json` points to the manifest and
reports exact per-document coverage. Collection is local-only; the main staged
runner performs formal artifact registration and database import separately.
