# ID_TH weekly archive reuse

Source: [xmusphlkg/ID_TH](https://github.com/xmusphlkg/ID_TH), a user-curated
archive derived from Thai official DDS Tableau downloads. This is a separate
source contract from direct official DDS/Report 506 ingestion. Its dataset
scope is `th_id_th_history`, ontology source `SRC_TH_ID_TH_ARCHIVE`.

The reviewed upstream commit is `da873f2a300f01dbc72e8eab5340a37ed62d7b75`.
The archive has 56 named disease categories, Gregorian 2020–2025 (source
Buddhist years 2563–2568), with case and death CSVs in separate directories.
All 672 attributable national weekly files contain 31,670 explicit national
weekly observations. For every file the annual national total reconciles
exactly to its published national weekly rows. There are 338 sparse files:
absent source weeks are not converted to zero. Explicit zeroes are retained.

Only four-component `Data/WeeklyCasesData/{year}/{disease}.csv` and
`Data/WeeklyDeathsData/{year}/{disease}.csv` paths enter the national extract.
The parser selects `จังหวัด-value=%all%` and a numeric source week. Province
rows, annual totals, and age subdirectories cannot double count the national
weekly observations. Annual totals serve only as reconciliation checks.

Thirteen files are audited exclusions: the six case and six death copies of
`เล_อกท_กคร_งเพ_อแสดงอ_ตราป_วย` are a Tableau UI instruction about incidence
rates, and the extra `ตารางการกระจายผ_ป_วยจ_งหว_ด_data` death worksheet cannot
be attributed to a disease. Every other named source category is retained,
including future new categories; overlapping categories are not merged.

## Time and reporting basis

The committed downloads do not prove whether the source Tableau view used
onset date/place or report date/place. The archive script offers an optional
report-basis filter but its default does not set it. The adapter therefore
uses `ReportingBasis=unverified_archive_basis` and never merges these facts
with a direct feed of known reporting basis.

No verified official 2020–2025 annual week-calendar artifacts were available
at onboarding. Official DDS historical calendar endpoints for y63–y68 return
HTTP 500. The available 2026 workbook's stale 2025 helper worksheet is not
published 2025 calendar evidence. Source 2025 week 53 contains nonzero values,
which also rules out silently treating these source weeks as ISO weeks.

**January 1 timestamps are source-year storage anchors, not observation dates.**
The true source year, week number and metric are stable observation dimensions
and enter the database natural key. All weekly rows remain distinct, including
weeks 1 and 53 in the same source year. No calendar dates or period boundaries
are invented. Each observation persists `CalendarStatus`, `TimeBasis`,
`DateRole`, source commit/path/Git blob identity and SHA256 in its raw-data
provenance. Case and death definition versions create separate holding series.

This dataset remains internal (`public_release_enabled=false`) with source
native unmapped holding series. A weekly plot must use explicit source year/week
labels, or wait for a verified calendar and an explicit migration of storage
identities. A future calendar must not simply change Date during a normal
upsert, since that would create second identities alongside the anchors.
The repository's R analysis produces interpolated daily/monthly values; these
modelled values are not imported as observed surveillance.

## Capture and daily revision checks

The connector retrieves GitHub's current main commit and its exact recursive
Git tree. Only missing or changed top-level national weekly Git blobs are
downloaded. Existing local snapshot/cache bytes are reused only after checking
both byte size and Git's `SHA1(blob length + NUL + bytes)` identity. New downloads
use commit-pinned raw URLs and the same verification. Each run captures commit,
tree, 12 deterministic metric/year ZIP bundles of unchanged original CSV bytes,
and an exclusion/collection audit as immutable artifacts. Member manifests retain
path, Git blob SHA, SHA256 and byte size; no CSV is regenerated or transformed;
organize replays those bytes without network access and requires a complete,
correctly pinned selection. The raw artifact store deduplicates repeated bytes.

`th-id-th-history-daily` checks revisions at 10:30 Asia/Bangkok. The separate
schedule setup script configures it; creating a job alone does not prove a live
scheduler or completed ingestion. Deleted upstream rows are not automatically
deleted from the database. Repository reuse/licensing remains a separate review
from the original official data, and no public release is enabled here.

Implementation: `src/data/crawlers/th_id_th.py`,
`src/data/processors/th_id_th.py`,
`src/services/ingestion/countries/th_id_th.py`.
Contract tests: `tests/unit/test_th_id_th.py`.

## Verified database import on 2026-11-15

Task `09f00822-bb79-4836-97d4-9c933b172328` completed all three ingestion
stages; CrawlRun `29373` stored 31,670 observations in 112 holding series.
The database contains 56 original disease codes in each case/death measure,
covering all six source years. All 31,670 natural keys are distinct and all
31,670 observations retain the full time/provenance fields described above.
17,218 values are explicit source zeroes. The 2025 measles case observation
for source week 53 remains 5; its death counterpart remains 0.

The fail-closed quality guard accepted the import. It retained nine warnings
for a single source period being zero across many disease categories. These
cells are explicit source zeroes, and all 672 disease/year/metric annual totals
reconcile; no missing periods were generated to produce those values. All
112 registry categories persist notifications disabled, and no source mapping
notification outbox events were created.

Capture used 15 immutable artifacts (three JSON records and 12 metric/year
ZIP bundles), totaling 11,356,257 bytes. All 672 CSV blobs were verified and
reused from the downloaded snapshot; zero CSV blobs were downloaded again.
The source repository carries a GPL-3.0 license file; that repository license
does not independently establish the original official dataset's reuse terms.

Detailed database evidence:
`reports/onboarding/th_id_th_database_20261115.json`.

An additional monthly-source check found that the archive's
`Data/CleanData/Influenza_mcd.csv` includes 2024 columns, but its underlying
`Data/GetData/Influenza/mcd_Influenza_67.rtf` was generated on December 25,
2024. The annual file is not a closed-year report and the existing Report 506
parser rejects it. It was not used to fill the official monthly history's
2024 gap.
