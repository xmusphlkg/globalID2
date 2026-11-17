# South Africa: NICD NMC surveillance

The official NICD Notifiable Medical Conditions Surveillance System archive is
connected as `za_nicd_nmc` / `SRC_ZA_NICD_NMC`, with persisted source label
**South Africa NICD NMC**. It retains national and nine provincial observations
as source-native holding series. Public release and disease-mapping notifications
remain disabled pending source/definition/licence review.

## Captured archive and numerical coverage

Verified on 2026-11-16:

| Item | Result |
|---|---:|
| Distinct reports in the current official directory | 37 |
| Additional historical originals in official WordPress media | 3 |
| Captured originals | 40/40 (39 PDF, 1 DOCX) |
| Originals with at least one verified numerical table | 40/40 |
| Retained source observations after edition selection | 17,013 |
| Numeric source observations | 16,774 |
| Explicit unknown cells | 239 |
| Explicit numeric zeroes | 9,106 |
| Native series identities, including geography/definition | 3,232 |
| Equal-priority conflicting facts retained | 0 |
| Representative observation dates | 2023-02-01–2026-08-01 |

The first current reporting publication is March 2023; February 2023 is its
explicit prior-month comparison. The directory currently ends in August 2026.
No October–December 2025 monthly reports were discovered. January–March 2026 is
one quarterly publication. Missing months are not zeroes.

**Complete original capture is not complete numerical coverage.** Verified
condition/province tables, explicit national totals, case-definition columns and
reconciling classification/vital-status groups are organized. Demographic,
incidence/rate, population, timeliness, back-capture appendix and chart data remain
raw evidence. Unverified layouts, incomplete condition labels and ambiguous
positional cells are withheld with reasons in the source audit. Every retained
condition label is checked against the complete labels actually published in
these official tables; new or truncated labels fail visibly rather than creating
an unintended disease series.

## Official update channels

- Directory and archive: https://www.nicd.ac.za/nmc-overview/nmc-monthly-surveillance-report/
- Public official media discovery: https://www.nicd.ac.za/wp-json/wp/v2/media?search=NMCSS&per_page=100
- Additional official media discovery: https://www.nicd.ac.za/wp-json/wp/v2/media?search=Notifiable-Medical-Conditions&per_page=100

Each live staged collection reads the directory and both media searches again,
following `X-WP-TotalPages`. It never treats the current 40-report snapshot as a
permanent catalogue. Unrelated annual reports, job notices and infographics are
excluded. `start_year` prunes only confidently dated catalogue titles; ambiguous
titles remain eligible, and organizing then filters verified source periods.

Report bytes use an integrity-checked, content-addressed disk cache. The default
`cache_ttl_hours=24` bounds reuse. Expired or forced entries are revalidated using
ETag/Last-Modified where available; HTTP 304 preserves original retrieval time and
records new revalidation time. Changed originals receive a new SHA-256 object;
previous bytes remain available. `force_refresh=True` or `force=True` forces
source revalidation. Both cache reuse and offline replay verify byte size and
SHA-256. Tampering fails visibly.

## Count and period contracts

- NMC notifications include infectious conditions, poisoning and maternal death.
  Clinical/laboratory notifications are not silently called confirmed cases.
- Province keys are `country:ZA:province:EC/FS/GP/KZN/LP/MP/NC/NW/WC`; national is
  `country:ZA:national`. A national total is retained only when explicitly printed.
- Province all-notification counts, national case-definition counts, modern
  classification tables and prior-month comparisons have separate identities.
  Their cohorts cannot be added together.
- Suspected/probable combined definitions and the asymptomatic-carrier column
  remain explicit. Category changes follow each report's source captions.
- Death counts represent patient vital status at notification. They are not
  assumed to be deaths occurring during the named month or final outcomes.
- Zero is retained as zero. Blank, dash, `n/a` and starred TB cells are unknown,
  never zero. The source TB-module limitation is not renamed suppression.
- Monthly `Date` is a representative first-of-month key. Exact monthly boundary
  fields are intentionally empty because some named months use ISO-week windows.
  Source month and any explicit ISO wording are retained in `SourcePeriodLabel`,
  `SourceWindowDetail` and dimensions. No epiweek-to-calendar conversion is made.
- Explicit Q1 January–March 2026 bounds remain 2026-01-01–2026-03-31 and quarterly;
  the quarter is never split into invented monthly values.
- Source diagnosis/notification/comparison time bases stay distinct in the series
  identity. The prior-month notification comparison is not used to revise a
  diagnosis-window series.

Examples of source inconsistencies are retained in the audit: August 2026
Shigellosis provinces sum to 46 while its printed national total is 49; national
49 is retained and the provincial group is withheld. Q1 2026 Diphtheria and
Meningococcal classification totals disagree with their patient-status totals;
only independently reconciling groups are retained. June 2023 has conflicting
category heading/caption metadata. No numerical or categorical correction is
invented. In malformed Word-generated PDF grids, missing cells cannot shift the
next province or classification value into a national-total column.

Current directory links take priority over older alternate originals within the
same report period. Official publication metadata resolves remaining edition
order. Retrieval time does not authorize historical changes. Conflicting facts
with equal source priority are withheld.

## Evidence, replay and verification

- Raw originals, hashes, catalogue responses and capture receipts:
  `data/raw/za/nicd_nmc_full_history/`
- Durable replay manifest:
  `data/raw/za/nicd_nmc_full_history/artifacts_manifest.json`
- Review CSV: `data/current/za/south_africa_nicd_nmc.csv`
- Coverage and per-table issues: `data/current/za/source_audit.json`
- Authentic fixtures and original-page/hash provenance:
  `tests/fixtures/ingestion/ZA/`

```bash
# Live discovery and complete public original capture
venv/bin/python scripts/collect_za_nicd_nmc.py --download --organize
# Offline integrity-checked replay of captured originals
venv/bin/python scripts/collect_za_nicd_nmc.py --organize
# Recovery may reuse verified originals, while catalogues still refresh live
venv/bin/python scripts/collect_za_nicd_nmc.py --download --resume --organize
venv/bin/python -m pytest -q tests/unit/test_za_nicd_nmc.py
```

The parser/cache/collector suite passes **17 tests**, using unchanged authentic
source pages and the complete official alternate DOCX. It checks comparisons,
zeroes versus true blanks, reversed columns, suspected/probable semantics,
quarters, carrier classification, independent cohorts, real inconsistent totals,
missing-grid-cell shifts, edition conflicts, live pagination and discovery,
start-year handling, TTL/304/new-version capture and offline corruption rejection.

Database import is performed by the shared foreground importer after parser
freeze; the CSV counts above are preparation evidence, not a database acceptance
claim.
