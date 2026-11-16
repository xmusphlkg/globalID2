# Colombia INS SIVIGILA historical municipality/week records

Reviewed 2026-11-16 against the live official [Colombian public health surveillance
dataset](https://www.datos.gov.co/d/4hyg-wa9d). This is an INS-authored historical
SODA release, not a live 2026 national notification feed. The source declares
CC BY-SA 4.0 and attribution to Instituto Nacional de Salud (INS).

## Source contract

- Scope: `co_ins_sivigila_historical`; source ID: `SRC_CO_INS_SIVIGILA`.
- Metadata: `https://www.datos.gov.co/api/views/4hyg-wa9d.json`.
- Rows: `https://www.datos.gov.co/resource/4hyg-wa9d.csv`.
- Source columns: event code/name, epidemiological year/week, occurrence
  department/municipality code/name, and reported event count. Socrata row ID
  is requested for traceability. All source categories, including noninfectious
  public-health events, remain source-native holding data pending mapping review.
- The live version has 2,465,826 source rows for 2007–2022. No later source
  year was present in the reviewed release. It contains 77 source event codes
  and 1,295 municipality/occurrence codes. The live source query found no event
  code associated with multiple event labels. Historical rows may be revised;
  `rowsUpdatedAt` and `viewLastModified` bind each bounded batch to one source
  version. A changed version requires a new complete pass, not mixing old and
  new pages.

The source is grouped by event, epidemiological week and **municipality of
occurrence**. Each row is retained independently under a source municipality
key. Source codes ending in `000`, source unknown-location labels and foreign
occurrence rows use separate keys. These observations are not automatically
summed to national counts: published municipality coverage and handling of
unknown/foreign occurrence have not been established as a complete national
denominator. The source count is preserved exactly; absent event/week/place
cells are unknown, not zero.

The dataset contains week 53 in 2008, 2014 and 2020. Its metadata gives week
numbers but does not provide dated week boundaries. The private series uses a
synthetic ordinal date anchored at January 1 plus seven days per source week
for storage ordering. Every row explicitly records
`date_role=synthetic_week_ordinal_anchor_not_event_date` and
`source_calendar=week_boundaries_unverified`; the date must not be presented as
the clinical event date or published as an exact weekly calendar until an
official source calendar is verified.

## Capture, import and revision

`src/services/ingestion/countries/co.py` captures metadata, annual row count,
one bounded CSV page, and post-download metadata before offline organization.
CSV pages are ordered by Socrata row ID, limited to 20,000 rows per batch. The
organizer checks the source version, requested year/offset, complete schema,
page length, SHA-256, integer counts, event identity and geographic code.
Network and persistence failures do not become missing zeroes.

The resumable historical controller captures and validates up to three source
years concurrently, then uses one normal staged database writer per page. The
country mutation lock serializes CO writes, so additional database writers
would not increase throughput. Immutable source manifests and per-year states
live under `data/raw/co/sivigila-history/<version>/<year>/`; the aggregate
progress is `data/current/co/history-state.json`. Every applied page has its
own database receipt next to its manifest, and the latest receipt for each year
is mirrored under `data/current/co/years/`. A run is bound to the official
source version and year counts. Restarting the same command resumes unapplied
pages; a changed source version starts a new archive. If a process exits after
database commit but before page-state persistence, replay checks the archived
Socrata IDs against stored date, geography, count, source identity and page
hash before marking that page applied. An existing per-page receipt is reused
only for the same manifest. `SIGUSR1` requests a cooperative stop after the
current page has been imported and recorded.

```bash
# Full 2007–2022 capture and database import. Restart to resume.
venv/bin/python scripts/imports/co_sivigila_history.py \
  --start-year 2007 --end-year 2022 --page-limit 10000 \
  --capture-workers 3 --apply

# Bounded validation or replay of one batch.
venv/bin/python scripts/imports/co_sivigila_history.py \
  --start-year 2007 --end-year 2022 --page-limit 10000 \
  --capture-workers 2 --max-batches 2 --apply

# Read-only revision check for a new official dataset version or year counts.
venv/bin/python scripts/imports/co_sivigila_history.py \
  --start-year 2007 --end-year 2022 --check-only
```

The controller state records every successful task and manifest. Source-native
facts use private holding series, and public release remains disabled until
calendar, geographic completeness, disease mapping and a generated snapshot
are verified. Metadata polling can detect a changed source version without
assuming this historical release covers newer years.
Routine metadata polling can skip import only when source version and all year
counts match a **complete** controller state and the independent database
acceptance also passes for that version. A partial import never qualifies for
the no-op path. `src/services/ingestion/co_update_gate.py` captures and audits
this lightweight source check through the normal task runner. On a changed or
incomplete history it reports `requires_full_backfill`; the version-bound
controller above performs the actual page replay. The bounded page collector
alone is not a safe daily refresh.

Use `venv/bin/python scripts/verify_co_sivigila_history.py --require-complete`
after the full run to compare each year's stored rows against the frozen
official count. Its independent receipt is
`data/current/co/database_acceptance.json`.

Focused parser checks: `venv/bin/python -m pytest tests/unit/test_co_sivigila.py -q`.

## Full historical acceptance (2026-11-16)

All 254 frozen pages for 2007–2022 were imported: 2,465,826 official rows and
2,465,826 database observations. The final full-row verifier independently
reparsed every archived page and compared the source record, stored date,
geography, event identity, definition, value and dimension identity. All 16
years passed, with zero missing, unexpected, duplicate or mismatched rows.
The observations comprise 2,423,515 municipality rows, 29,752 unknown-location
rows and 12,559 exterior rows. Evidence: `data/current/co/history-state.json`
and `data/current/co/database_acceptance.json`.

The live metadata gate also passed against this complete version, producing
`unchanged_verified_metadata_only` without downloading any case pages; its
captured evidence is referenced by `data/current/co/update_check_acceptance.json`.
The saved daily metadata check is enabled at 11:15 America/Bogota. If INS
changes the dataset or its year counts, the task reports `requires_full_backfill`;
the resumable controller and full verifier must then be run again. This daily
check does not automatically launch the multi-hour backfill. Public release
remains disabled because the weekly calendar and national aggregation contract
remain unverified.
