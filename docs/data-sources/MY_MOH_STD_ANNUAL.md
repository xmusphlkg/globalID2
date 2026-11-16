# Malaysia MOH annual STD notifications

Verified 2026-11-15 against the live [MOH/data.gov.my catalogue](https://data.gov.my/data-catalogue/std_state)
and its [complete Parquet release](https://storage.data.gov.my/healthcare/std_state.parquet).

- Canonical scope: `moh_std_annual`; source ID: `SRC_MY_MOH_STD`.
- Adapter: `src/data/crawlers/my_notifiable.py`; updater: `MYAnnualUpdater`;
  staged collector/organizer: `src/services/ingestion/countries/my_notifiable.py`.
- Licence: CC BY 4.0; attribution: Ministry of Health Malaysia.
- Source release last updated 2026-10-11; data coverage 2017–2024. The source
  catalogue currently announces its next update for 2026-12-31.

## Reporting contract

The upstream dataset covers five STD categories: `aids`, `chancroid`,
`gonorrhea`, `hiv`, `syphillis`. All published categories are retained;
additional future categories enter the source-native mapping review flow.
HIV and AIDS remain distinct categories. `syphillis` retains its original
upstream spelling in `SourceDiseaseCode`; its display label is Syphilis.

`cases` is the annual number of reported notifications, by report date.
`date` is January 1 of the calendar year. `incidence` is a rate per 100,000
population and never replaces the count. Missing counts remain unknown with
`MissingValueStatus=source_missing`; observed zeroes remain zero. No absent
disease/year cell is synthesized. All rows use annual frequency and explicit
notification reporting basis, definition version and source SHA-256.

Only explicit `state=Malaysia` rows become national facts. State rows are not
added to their national aggregate. Source metadata says W.P. Putrajaya is
included in W.P. Kuala Lumpur. The complete release contains 640 rows; the
national selection contains 40 rows, eight annual observations per category.

Live source totals across the eight years, useful as fixture checks:

| Source category | Notifications |
| --- | ---: |
| aids | 8,964 |
| chancroid | 16 |
| gonorrhea | 22,399 |
| hiv | 25,692 |
| syphillis | 31,516 |

## Revisions and release state

Each collection captures the full immutable Parquet response before offline
normalization. Each run rereads the full published history, allowing revised
counts to upsert identical source/disease/year facts. Monthly scheduling
windows do not discard annual January date stamps. The database latest-date
query is restricted to this source system.

The default commit policy performs upserts. It does not claim authoritative
deletion for a disease/year removed entirely from a future source release.
Source-null observations are retained in the normalized row with explicit
missingness; unknown-category missing rows may require registry support before
they can supersede an existing holding observation. Public release remains
disabled until a successful import and verified country snapshot exist.

## Remaining national coverage gap

This dataset is an STD feed. It does **not** cover every Malaysian notifiable
infectious disease. MOH Health Facts/Health Indicators are candidates for the
remaining diseases. During this verification, official MOH pages `/pages/view/58`
and `/pages/view/260` and the attempted 2025 Health Facts PDF returned HTTP 403;
the legacy `archive.data.gov.my` CKAN endpoint failed TLS negotiation.
No broad multi-disease machine contract is asserted from those unavailable
resources. Existing coverage must not be described as complete national
notifiable-disease surveillance.
