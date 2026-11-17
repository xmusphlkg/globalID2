# High-frequency public export acceptance

`scripts/verify_high_frequency_public_exports.py` is a read-only acceptance pass for every source registered in `HIGH_FREQUENCY_SOURCES`, plus the Austria AGES monthly source. It does not write database rows, mapping releases or website files.

The verifier runs each country in a repeatable read-only transaction and records the active mapping release checksum. It queries the source ledger directly, derives reviewed concept targets from the isolated ontology and mapping patches, then compares every admitted source point with `fetch_disease_export_layers`. The comparison includes source ID, series identity, disease target, frequency, typed metric and units, reporting geography, date, dimensions and value. Suppressed and missing cells are counted separately and excluded from numeric point matching.

Case curves are checked against their source point for the exact date and series. Laboratory detections/tests/positivity and healthcare activity rates cannot satisfy a case projection. CZ, EE, MX weekly/monthly notifications and AT AGES are required to produce reviewed numeric points when their source rows and mappings are active. CO is an independently managed integration; absent facts or mappings are reported as `pending_external_integration` and do not block the original country batch.

The report includes each source's observed period coverage, explicit zero/missing/suppressed counts, metric/frequency/unit distribution, internal calendar gaps, excluded rows and a coverage note that distinguishes observed source periods from archive completeness. With `--public-dir`, the verifier additionally checks `countries/<code>.json` and `<code>-source-series.json`, including point source dimensions and curve values. It never treats an inferred gap as zero.

Run after the mapping release activation:

```bash
venv/bin/python scripts/verify_high_frequency_public_exports.py \
  --receipt reports/high-frequency-public-export-acceptance.json \
  --public-dir <generated-public-data-directory>
```

The pure comparison and contract tests are in `tests/unit/test_verify_high_frequency_public_exports.py`.
