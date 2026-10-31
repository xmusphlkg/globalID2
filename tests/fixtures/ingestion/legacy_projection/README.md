# Persisted legacy projection contracts

This directory belongs only to `test_legacy_projection_contracts.py`. Tests read
these files; they never create, update, or accept expectations automatically.
`UPDATE_INGESTION_GOLDENS` is not used by this harness.

## Independent baseline

`contracts.json` was hand-authored from the small source fixtures and the legacy
rules in commit `d1549f0101ee1b908d420375e7fce46682c90b59`. No current parser,
record builder, or import method generated its expected records. Numeric country
and disease IDs are synthetic, stable registry identities, not production IDs.

Source provenance and independently checked expectations:

| Input | Expected legacy projection |
| --- | --- |
| `US/current.csv` | US RESIDENTS, 2025 week 1: 2 cases. TOTAL=3 and state rows cannot replace it. |
| `US/nhss_2024.xlsx`, Table A1a | 2023: 38831; 2024: 38434, dated December 31 at 12:00 UTC. |
| `US/archive.json` and current state rows | US child runs are series-only, so their expected legacy set is empty, even with nonempty source rows. This is explicit in baseline `USWeeklyUpdater.fetch_latest`. |
| `JP/weekly.csv` | National AIDS=2, ISO week 12 of 2025 ends Sunday March 23 at 00:00 UTC. Prefectures are series-only. |
| `AU*/location.json` | March 2026: AU=39, ACT=0, NSW=12, VIC=2, QLD=3, SA=4, WA=5, TAS=6, NT=7. Each jurisdiction has its own country ID. |
| `CN/report.html` | February 2025: Hepatitis A=0/0 cases/deaths, B=NULL/0, C=4/0. |
| `cn_edges.html` | Same mapped cases, B has NULL deaths, C has 2 deaths, and the unmapped disease has no legacy row. |
| `jp_edges.csv` | National week 12=2 and week 13=0; blank week 14 and unmapped disease produce no record. Tokyo=999 cannot replace a national value. |
| Frozen `NZ/golden.json` rows | March 2026: COVID-19=5, Campylobacteriosis=0. |
| Frozen `TW/golden.json` rows | January/February 2026: AIDS=5/0. |
| Frozen `HK/golden.json` rows | January/February 2026: Paratyphoid fever=1/0. |
| Frozen `FI/golden.json` rows | July 2026: total tuberculosis=3, pulmonary tuberculosis=0; distinct mapped groups stay distinct. |
| Frozen `SE/golden.json` rows | National January/February/March 2025=431/398/405; county rows do not enter the legacy table. |
| Frozen `AT/golden.json` rows | Empty legacy set: source-native AGES facts are written to the series table by the surrounding service. |
| Frozen `CA-ON-file/golden.json` rows | January Measles/Mumps=2/1; February=0/0, all under Ontario rather than Canada. |

Existing country goldens are used only as immutable *input rows*. The expected
five-tuples are stored independently here. This test never invokes the shared
golden writer or modifies another task's fixtures.

## Behavioral coverage

- Actual is a SELECT of all committed `disease_records` rows from a separate
  session, compared on `(time, disease_id, country_id, cases, deaths)`.
- Production insert/upsert statements run against the production record-table
  schema in an isolated `sqlite+aiosqlite:///:memory:` database. Composite keys
  are enforced by SQL, rather than emulated with a dictionary of parameters.
- US uses its source-specific eight-week revision window, including the exact
  boundary and forced imports. Annual NHSS revisions bypass the weekly cutoff.
- JP checks both row-level and whole-source date gates, and forced imports.
- CN's real `check_new_data` reads a fixture database before the old report can
  reach parsing. A later province record must not advance the national cursor.
- Monthly sources keep their existing revision rules when the database cursor
  is newer; replays and changed values upsert without accumulating prior counts.
- Explicit zero, empty and null cells, unmapped labels, many-to-one mapping
  aggregation, FI collision rejection, and Ontario blank-batch rejection and
  suppression are covered separately.

The empty/null policies deliberately differ by source: AU preserves NULL; NZ,
HK and TW project missing cases to zero; FI and SE skip missing cases; US skips
missing cases. These are baseline legacy behaviors, not new normalization rules.
CN preserves paired deaths; case-only sources keep deaths NULL.

## Scope and reproduction

```bash
venv/bin/python -m pytest -q tests/contract/test_legacy_projection_contracts.py
env -u UPDATE_INGESTION_GOLDENS SKIP_NETWORK_TESTS=1 venv/bin/python -m pytest -q tests/contract
```

The harness forbids PostgreSQL connections and HTTP requests. Registry lookup
I/O is replaced with fixed mapping results; the real country-ID queries,
projection logic, SQL writes, commits, and post-commit reads execute locally.
The service integration tests isolate source-series writes and dirty marking to
exercise the legacy side of `UpdaterCommitStrategy` and
`ChinaReportCommitStrategy` without a second schema/registry implementation.

SQLite cannot validate PostgreSQL advisory locks, TimescaleDB behavior,
PostgreSQL JSON casts, or transaction concurrency. CN adjacent-day cleanup uses
PostgreSQL `ctid` and is stubbed: these report fixtures have no adjacent-day
duplicates. Source-series quality and registry coverage remain the existing
observation tests' responsibility. None of this constitutes production replay
or deployment acceptance.
