# Cambodia CDC CamEWARN weekly history

Official index: <https://cdcmoh.gov.kh/surveillance/camewarn>. Reviewed 2026-11-15.
Scope: `kh_camewarn_weekly`; source identity: `SRC_KH_CAMEWARN`; stored label:
`Cambodia CDC CamEWARN Weekly Surveillance`.

## Coverage and source contract

The live index links nine historical archives: 2015–2022 ZIP releases and a
2025 RAR, plus directly linked 2025/2026 weekly PDFs. All linked targets are
discovered from the captured index; absent years, weeks and guessed filenames
are never synthesized. The 2015 ZIP contains `NOTE.txt` explicitly stating
that weeks 44–47 were not uploaded because of a system problem.

CamEWARN is reporting-facility surveillance, not a count of every infection in
Cambodia. Its disease and syndrome categories are retained source-natively.
The early matrices include ten categories; later reports have seven. Acute
diarrhea, fever with rash, severe respiratory infection, acute flaccid paralysis,
suspect dengue fever, meningitis or encephalitis, acute jaundice, diphtheria,
rabies and neonatal tetanus must not automatically become confirmed diagnoses.
Acute hemorrhagic fever and suspect dengue labels remain distinct native keys.

The 2015–2018 reports reviewed contain national weekly case matrices, often
including previously published weeks. Exact cells, including explicit zeroes,
are read by source week. Cumulative `Total` columns and future template cells
are excluded. Some templates retain stale totals in blank future blocks; those
are never interpreted as case observations. Later reporting weeks revise
earlier weekly cells; contradictory copies of the same reporting edition fail.

Recent reports usually contain raster charts rather than machine-readable
case tables. A verified disease heading establishes a published category/week,
but the chart's magnitude is not estimated. These rows have empty `Cases`,
`MissingValueStatus=source_missing` and `chart_only=true` in their dimensions.
Completeness/timeliness tables count submitted reports and are never cases.
Per-facility appendices are preserved in raw PDFs; they are not silently summed
into national totals without an explicit, complete geographic contract.

## Calendar, raw evidence and revision behavior

Dates come from each PDF's explicit seven-day interval. Early CamEWARN weeks
run Wednesday–Tuesday: source week 1 of 2015 starts 2014-12-31. They must not
be reassigned ISO Monday dates or filtered using calendar-year stamps alone.
Original reporting year/week, period start/end, report edition, archive member,
source URL and raw SHA-256 are retained on every row. An explicitly conflicting
header year, invalid interval or unverified period is rejected and audited.

The official HTML directory contains incorrect aliases (for example W31 links
to W25, and W42 links to W41). Discovery preserves these aliases and deduplicates
URLs; report dates come from the PDF body. It does not fabricate the missing
weekly files. Reports in the archives can have their own source mistakes.

Collection captures the index, every linked archive, every complete PDF member
and a member/hash manifest before business parsing. Organization uses those
captured bytes only and verifies the discovery plan and member manifest.
Available valid reports survive independent source parse failures, but failures
remain visible. Partial responses do not withdraw previously stored windows.

RAR decoding requires `bsdtar` (`libarchive-tools` on Debian/Ubuntu). Without it,
the complete upstream RAR is still captured and the unavailable decoder is an
explicit collection error. ZIP handling reads member bytes directly, without
extracting archive paths. No login or private application export is used.

## Operations

The standard country connector is
`src/services/ingestion/countries/kh.py`; updater:
`CambodiaCamEWARNWeeklyUpdater`. Default discovery starts in 2015 and reads
all published history. Public release stays disabled while reuse terms,
canonical disease mappings and a generated snapshot remain unverified.

The dedicated review command archives and organizes without writing case facts:

```bash
venv/bin/python scripts/imports/kh_camewarn_history.py
```

Its manifest is under `data/raw/kh/camewarn-history/<timestamp>/manifest.json`;
the output CSV and full report audit are under `data/current/kh/`. File snapshots
explicitly declare `database_registered: false`; this command is not proof of a
database import. The normal staged connector uses the registered ArtifactWriter.
Previously downloaded archives can be passed with `--reuse-cache PATH`; cached
snapshots are labelled `cached_snapshot`, not reported as fresh source requests.
`--replay-manifest PATH` performs organization without network access.

Regression fixtures preserve real official PDFs and the source index. Run:

```bash
venv/bin/python -m pytest tests/unit/test_kh_camewarn.py -q
```

Actual row counts, observed values, chart-only unknowns, rejected files, native
category inventory and raw-manifest location are recorded in `source_audit.json`.
Full archival coverage does not mean every chart value has been digitized.
