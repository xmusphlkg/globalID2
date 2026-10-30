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
