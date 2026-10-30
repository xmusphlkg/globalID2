# Ingestion contract fixtures

Contract tests in `tests/contract/` compare the rows a country's three-stage
pipeline *stores* against a fixture of expected rows, so a switch to the
new pipeline cannot silently change an observation's natural key.

Fixtures are plain, in-memory Python structures (dicts or small dataclasses),
committed next to the tests under `tests/fixtures/ingestion/<key>/`. They
must not touch the network; the comparator (`tests/contract/compare.py`) only
reads fields by name.

## Directory layout

```
tests/fixtures/ingestion/
  README.md
  <key>/                # e.g. us, cn, jp
    expected_observations.py   # exposes EXPECTED_OBSERVATIONS: list[dict]
    expected_records.py        # exposes EXPECTED_RECORDS: list[dict]
```

A `<key>` fixture exists only for countries whose default pipeline has been
switched to three-stage (`FusedLegacy` keys never get one). The key is the
`_PIPELINES` key for that country, e.g. `US`.

## Comparison keys

- Observations: `(time, series_code, geography_key, dimension_key)` plus
  `value`, `suppressed`, `quality_status` and the metadata
  `missing_value_status`.
- Legacy records: `(time, disease_id, country_id, cases, deaths)`.

Times may be ISO strings or `datetime`/`date`; aware datetimes are folded to
UTC before comparing. `metadata` is read from the `metadata_` ORM attribute
or the `metadata` dict key.

## What a fixture must contain

Only the rows a country stores in its formal tables. Aggregate rows that the
pipeline excludes (e.g. US national totals) must not appear. If a later
mapping run is expected to introduce extra candidate rows, list those in the
country test's `ALLOWED_DIFFS` rather than weakening the fixture.

## Provenance

Fixtures are snapshots of the pre-switch `_execute_*` output for that key.
If the upstream source format changes, update the fixture in the same PR that
changes the parser, and call out the diff in the PR description.

ECDC annual fixtures cover all 31 reviewed country keys. Each country's
`ecdc.json` pins its source geography (including EL/UK aliases), and references
`ECDC/responses.json` for the complete 55-topic metadata and results. The fixture
contains explicit zeroes, missing cells, combined populations, and current-year
cells. Contracts retain pre-window history and reject withdrawn in-window cells.

`CA/describe.json` and `CA/raw.json` cover the complete 74-disease, 1924–2024
CNDSS grid. `DE/rki.zip` is a UTF-16 pivot export shared by national and 16
state-specific fixtures. `SG` captures historical CSV and annual workbooks,
including zero, absent future week scaffolding, and attendance columns. These
are synthetic fixtures for the reviewed source contracts, not claims of live
collection. All four adapters compare production registry observation keys in
addition to normalized source rows. Allowed observation differences: none.

Iceland fixtures cover `all`, `is_doh_annual`, `is_doh_sti`,
`is_doh_respiratory`, `is_doh_history`, and `is_doh_legacy_icd`.
`IS/current_queries.json` contains raw DSR responses for every reviewed current
series, including respiratory ISO week 2020-W01 beginning in calendar 2019.
The three historical workbooks exercise annual not-applicable dashes, monthly
published-zero dashes, unknown blank cells, evaluated formulas, quarantine,
and ICD diagnoses that never enter the legacy case-notification projection.

Monthly source contracts cover AU and all eight state/territory keys, HK, TW,
AT, FI, NZ monthly, CA-ON official-file and live Power BI acquisition, and SE
plus every registered county. Their fixtures are synthetic source responses.
Each contract compares the staged organizer to the original parser on the same
bytes and then checks a fixed `golden.json` containing source rows and registry
observation natural keys, values, suppression, quality and missing-value status.
Source-native AT categories additionally compare their unchanged holding-series
source identities. Allowed observation differences: none.

Golden updates are deliberate test maintenance, never a normal test side effect:
`UPDATE_INGESTION_GOLDENS=1 venv/bin/python -m pytest tests/contract/test_<country>_observations.py`.
Review the resulting JSON diff before accepting a changed baseline. Changing a
shared parser cannot silently update the normal contract test's expectations.

`test_source_capture.py` also exercises real file/database registration for an
NZ sitemap descriptor containing a date, records invalid JSON before decode,
checks CA-ON metadata/query capture without retaining its bearer-token wrapper,
and requires raw-persistence failures to propagate even when a prior monthly
snapshot could otherwise recover the source run.
