# India IDSP: official weekly outbreak capture

The staged source `in_idsp_weekly` (`SRC_IN_IDSP`) discovers the official
[NCDC weekly outbreak archive](https://ncdc.mohfw.gov.in/includes/WeeklyOutbreaks.php),
captures its HTML and PDFs, then extracts verified district outbreak snapshots
without network access during organization. The country adapter is
`IndiaIDSPUpdater`; public release remains disabled.

## Verified source, 17 November 2026

The NCDC homepage links the archive above, which returned HTTP 200 with weekly
PDF links spanning 2009–2026. The archived listing currently contains all 52
weeks of 2025 and weeks 1–37 of 2026. Discovery does not manufacture later weeks.
The older supplied IDSP domain failed TLS handshakes from this execution host;
the working NCDC archive provides an official replacement without bypassing
authentication.

The saved real [2026 week 37 PDF](https://ncdc.mohfw.gov.in/uploads/weekly_outbreaks/2026/week37_1794379575.pdf)
explicitly covers 7–13 September 2026. Its main table has 44 outbreak events,
with cases/deaths in separate columns. A separate late-reported-outbreak annex
contains two additional events and uses different columns. The parser captures
this annex in raw bytes but excludes its numerical facts pending separate
layout review. Fixtures include the real archive HTML, real PDF and a provenance
receipt; offline tests verify numerical values, geography, cover dates, split
IDs, annex separation, conflict handling and staged replay.

## Statistical contract

- These are **outbreak event snapshots**. Event onset can precede the reporting
  week. Cases and deaths are not asserted to be new incident cases/deaths during
  that week, and must never be summed into routine national incidence.
- `Frequency=weekly` describes the report cadence; `PeriodType=snapshot`,
  `ReportingBasis=outbreak_event_snapshot` and the explicit `event_id` dimension
  preserve the statistical grain. Disease and measure remain separate source
  category identities. No clinical mapping is declared before review.
- The snapshot date is the PDF cover's explicitly printed period end; its
  source week and explicit date range remain available. No ISO-week dates are
  guessed from directory labels, including cross-year weeks.
- District and state abbreviations are **source codes**, not ISO subdivision
  mappings. `series_geography_from_rows=True` prevents a national default.
- An explicit zero in an event's deaths cell is retained. Missing reports,
  missing/ambiguous cells, NIL outbreak submissions and reporting-unit coverage
  statistics never become zero disease counts. Unsupported table formats remain
  audited omissions.
- Conflicting observations for the same report/event/disease/measure are
  quarantined. This partial collector does not replace an authoritative window
  or withdraw existing observations when discovery/download/parsing is partial.

## S/P/L coverage gap

The [NCDC programme page](https://ncdc.mohfw.gov.in/includes/About/CentresAndDivision/IDSP.php)
confirms that the system distinguishes syndromic **S**, presumptive **P**, and
laboratory-confirmed **L** surveillance. These have separate definitions in the
adapter contract. Its public programme coverage percentages are administrative
indicators, not weekly disease counts. Outbreak PDFs do not identify their event
case columns as any of these forms. Every parsed event therefore retains
`surveillance_form=not_reported`; the updater refuses routine-form rows. Routine
S/P/L numerical ingestion requires independently verified public datasets,
separate source categories and definitions. Authenticated IHIP APIs have not
been called.

## Collection and replay

`collect(writer, source="in_idsp_weekly", start_year=2009, end_year=None,
max_reports=8)` captures the newest bounded selection. `max_reports` must be
1–1000; increasing it explicitly permits historical artifact replay. The saved
plan records the requested years, available report count, selection limit,
download worker count, reports omitted by the limit and download failures. PDF
downloads use bounded per-worker sessions and the shared conditional cache with
a default 24-hour revalidation interval; `force=True` forces revalidation.
Every selected response, including a cached PDF, is written as an artifact for
that run before parsing.

Only the reviewed landscape ten-column geometry is parsed. Older and revised
layouts are captured but rejected or partially audited, so archive availability
does not imply numerical historical completeness. The verified 2020 and 2026
rows are now in the internal source-series ledger (628 and 250 rows; see
`data/current/in/idsp_2020_import_20261117.json` and
`data/current/in/idsp_2026_import_20261117.json`). Source rights, reviewed
clinical mappings and a public snapshot remain separate acceptance steps.
Offline organization uses a parser-versioned cache under
`data/cache/in/idsp-parses`; cached results are accepted only when both the
captured PDF hash and parser hash match, and the audit reports cache hits and
misses for reproducibility.
