# Austria AGES Radar monthly source

Checked on 2026-11-17 against the live [AGES Radar issue archive](https://www.ages.at/en/human/disease/ages-radar-for-infectious-diseases).

## Verified source coverage

Live capture and checksum-verified offline organization produced **441 numeric facts**, representing **63 original disease categories across 7 calendar months**: January–June and September 2026. The CSV discovery did not provide July or August source tables. These months must remain unknown; do not interpolate, difference cumulative columns, or replace them with zeroes.

Manifest: `data/raw/frequency-expansion/20261117T013647Z-at/at/ages_radar/artifacts_manifest.json`.

Normalized output: `data/current/frequency-expansion/20261117T013647Z-at/at-ages_radar.csv`.

The source table explicitly distinguishes the single report month, year-to-date totals, prior-year totals and historical median. Only the single month column is used. The parser verifies that its header agrees with captured issue metadata. Explicit zero and explicit missing cells remain distinct. A real September 2026 CSV, original URL and SHA-256 are preserved in `tests/fixtures/ingestion/AT/ages-live-202609.*`.

## Source contract and publication

Rows now explicitly retain monthly frequency, `case_notifications`, `count`, `national_notifiable_disease_radar`, calendar-month notification basis, Gregorian month start/end, original disease label/code dimensions and authoritative revision semantics. Cumulative columns never become observations. Category and geography dimensions are retained; age or regional rows cannot become national totals through the validator.

Publication follows the operator-authorized setting used for this integration. The underlying license field remains `not_specified_by_AGES`; this change makes no new license claim. Mapping notifications remain disabled.

`src/services/at_high_frequency_review.py` admits only exact reviewed category/definition identities with matching disease, units, national geography, original dimensions and a complete calendar month. It preserves the original dimensions and sets a separate verified national projection key. Unmapped categories remain source facts.

## Identity and patch boundaries

There are two existing AT storage populations: 441 numeric facts under `SER_AT_AGES_RADAR_SOURCE_NATIVE` and 189 numeric observations under 63 source-current holding series. The latter have `unknown` frequency metadata; their values are not missing. This integration preserves both populations and does not duplicate the numeric observations into new category holding series. `configs/high_frequency_at_ontology.json` only updates metadata on the existing static series; it adds zero new series. The mapping patch `configs/onboarding/high_frequency_at_mappings.json` contains all 63 actual source categories under definition `AT_AGES_RADAR_source_native`.

New rows explicitly resolve `SourceDiseaseCode=__source_native__` to the original static series and preserve the actual category slug in `Dimensions.source_disease_code` and `SourceCategoryCode`. The merge key uses reporting month and original category slug, so categories cannot collapse into one monthly row. Website validation requires the exact static series; the old holding population cannot duplicate it publicly. The separate `SourceContractVersion` records the parser contract revision without changing the storage definition identity.

`scripts/import_at_high_frequency.py` replays the checksum-verified capture through ordinary durable ingestion and independently verifies static series values, raw source fields, existing history retention and an unchanged fingerprint of the entire old holding population. It does not modify a mapping release or publish the website.

Absent source months must remain unknown. Neither the absence of a CSV month nor a category mapping authorizes synthesizing a value.

## Durable import verification

The formal replay completed on 2026-11-17 under task `d3100108-9a62-4db9-81e3-effbe0c1eeea`, crawl run `32748`. All 441 captured facts were verified against the database. The original static series grew from 441 to **504** observations, retaining 63 December 2025 historical facts and adding 63 September 2026 facts. Missing, mismatch and duplicate counts were all zero. The complete fingerprint of all **189** original holding observations remained unchanged.

Receipt: `data/current/frequency-expansion/at-database-import-receipt.json`.

The source gap in July and August produced calendar-gap warnings, with no values synthesized. The import did not publish a mapping release or website; those steps use the main task's release process.

Validation: 40 related tests passed, including 12 new actual-source and publication-contract checks plus existing parser, offline contract, capture, and staged monthly pipeline tests. The AT golden file was explicitly updated for the new metadata and unchanged source value/calendar identity.
