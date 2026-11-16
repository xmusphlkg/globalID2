# Netherlands RIVM annual notifiable diseases

Official source: [RIVM overview of infectious-disease notifications](https://www.rivm.nl/meldingsplicht-infectieziekten/overzicht-meldingen).
The page was retrieved successfully on 2026-11-15. Its complete public HTML
table contains 56 source categories for 2016–2025, selected from Osiris-AIZ on
2026-03-10. These are RIVM-approved notifications, assigned by first symptom
date, or diagnosis/notification date when symptom date is unknown. They are
neither estimated infections nor counts by publication date.

The canonical scope is `rivm_notifiable_annual`, source ID
`SRC_NL_RIVM_NOTIFIABLE`, and stored source label
`Netherlands RIVM Annual Notifiable Disease Notifications`. This national-source
contract is independent of the ECDC annual baseline. All 56 categories are
parsed, including uncommon diseases and explicit zeroes; no COVID-only subset
stands in for the national surveillance table.

## Geography and case definitions

The table's footnotes are part of the parser contract. Missing required notes,
changed category coverage, duplicate categories, malformed counts and truncated
year rows fail the whole refresh.

- Dengue and chikungunya notifications apply only to Caribbean Netherlands
  (Bonaire, Sint Eustatius and Saba). Their rows retain `country:BQ:national`;
  they must not enter Netherlands national totals. Other rows use
  `country:NL:national`. The updater derives geography from each row.
- Foodborne-infection clusters and extra-hospital MRSA clusters retain an
  outbreak-notification measure and `events` unit, separate from patient cases.
- Acute and chronic hepatitis, the three paratyphoid categories and both
  Creutzfeldt-Jakob categories remain distinct source identities.
- Invasive pneumococcal disease covers children born since 2006, extended to
  persons over 60 from 2021-04-01. The age-contract change creates a distinct
  definition version; the transitional 2021 year retains the effective date.
- Pregnancy-associated listeriosis notifications for mother and child are
  counted as one infection in the published statistics.

## Reporting windows and zeroes

Source zeroes are retained within the documented reporting contract. Zero
scaffolding before notification requirements, or after their cessation, is
excluded from observations and remains visible in the raw artifact:

| Category | Eligible source years and interpretation |
| --- | --- |
| COVID-19 | 2020–2023; mandatory reporting began 2020-01-28 and ended 2023-07-01. The 2020 and 2023 coverage windows are explicit; 2024/2025 zeroes are not full-year zero case counts. |
| Mpox | 2022 onward, beginning 2022-05-22. |
| CPE | 2019 onward, beginning 2019-07-01. |
| Tick-borne encephalitis | Voluntary notifications in 2023/2024; mandatory reporting from 2025-03-01, with a distinct definition and reporting basis. |

This produces 538 source observations across all 56 categories in the reviewed
snapshot. Blank/dash cells remain unknown with `MissingValueStatus=source_missing`;
they are never manufactured as zero. New or changed suppression notation fails
closed for review. Current calendar years are rejected until closed.

## Collection, storage and release

`src/services/ingestion/countries/nl_notifiable.py` captures the complete HTML
response before parsing and records the collection date and requested start
year. Organization reads integrity-checked artifacts without network requests,
using the captured date so historical replay cannot silently admit an open year.

The updater stores source-native facts through Mapping Registry v3. Unreviewed
categories enter non-projectable holding series; no automatic legacy disease
projection or automatic rollup is allowed. The source supports later additions,
corrections and withdrawals after investigation. Refreshes upsert revised cells;
they currently do not perform authoritative deletion of dates/categories absent
from a later table. A changed table structure is rejected rather than interpreted
as a deletion. A complete-window replacement policy requires a separate review.

This adapter is configured for internal onboarding. Public release is pending
source reuse review and a verified generated snapshot; the existing ECDC public
baseline can remain independently enabled. Source coverage through 2025 is not a
claim that current-year weekly national reporting has been integrated.

## Verification

```bash
venv/bin/python -m pytest tests/unit/test_nl_notifiable.py -q
```

The fixture in `tests/fixtures/ingestion/NL/rivm_notifiable_annual.html` preserves
the official table and explanatory notes from the retrieved page's main content.
Tests check every category, fixed known values, geographic separation, cluster
units, changing age/reporting definitions, invalid zero scaffolding, unknown
cells, contract drift and offline artifact replay/integrity.


## Caribbean Netherlands parent collection

BQ has a registered country and canonical scope for its native geography, while
its two RIVM categories remain owned by `SRC_NL_RIVM_NOTIFIABLE` and the NL
collector. The verified capture contains 20 BQ annual observations for
2016–2025. BQ remains private and has no independent crawl pipeline or job.

The existing four NL jobs retain their 30-minute intervals. Their persisted
next runs are staggered by six minutes to prevent same-country task deduplication
from repeatedly skipping the annual source. Rephase only when the country is
idle; operator pauses are never silently resumed:

```bash
venv/bin/python scripts/rephase_nl_refreshes.py                 # preview
venv/bin/python scripts/rephase_nl_refreshes.py --apply --verify \
  --wait-idle-seconds 600 --wait-complete-seconds 600
venv/bin/python scripts/rephase_nl_refreshes.py --verify        # read-only
```

`reports/onboarding/nl_bq_scheduled_refresh.json` records the actual scheduler
job, matching task country/source/job identity, all three completed stages,
original HTML hash and source-to-database comparison of the 20 BQ rows. An
unrelated same-country task returned by enqueue deduplication is insufficient
evidence that this source refreshed.
