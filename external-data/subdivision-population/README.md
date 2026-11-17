# Official population sources and automatic refresh

The tracked review baseline is `official_population_latest.csv`: 1,900
location/year rows for 68 jurisdictions, retrieved on 2026-10-06. The live database
and scheduler now also include US, JP and SE; use `scheduler-status.json` and
`latest.json` under `data/processed/official_population/` for current coverage.
The tracked baseline's input hash, source URLs, public query bodies, coverage and gaps are in
`official_sources_latest.json`. Original earlier yearbook/ABS files below remain
as historical references; the latest NBS API can revise past estimates.

| Source | Current coverage | Update method |
| --- | --- | --- |
| NBS China | National + 31 mainland provinces, 2000–2025 | Discover total-population indicators in the new public data portal; query explicit years and exact regions |
| ABS Australia | 8 states/territories, 1981–2026 | Discover newest published quarterly release and download table 310104 |
| IBGE Brazil | National + 27 states/federal district, 2000–2026 except 2007 and 2023 | SIDRA table 6579 annual estimates; tables 202 and 4714 census totals for 2000/2010/2022 |
| Census US | 50 states + DC, 2000–2025; Puerto Rico, 2010–2025 | Discover latest official population-estimate CSV vintage by decade, including official intercensal estimates; unsupported island areas remain missing |
| Statistics Japan | 47 prefectures, 2011–2024 | Discover official annual/e-Stat workbooks and official census backcasts; total population includes foreign residents, 1 October, published thousand-person precision |
| SCB Sweden | 21 counties, 2000–2025 | Discover historical and current CKM PxWeb tables, exact year-end resident totals; new years discovered from catalogue metadata |

```sh
# Discover, archive and validate each source independently; no database writes
venv/bin/python scripts/update_population_automatically.py
# Import official revisions and queue the existing release only when values change
venv/bin/python scripts/update_population_automatically.py --apply --queue-release
# Optionally refresh just one country source
venv/bin/python scripts/refresh_official_population.py --sources CN --apply --export
# Verify generated rates against the latest successfully applied population snapshot
venv/bin/python scripts/verify_subdivision_incidence.py
```

Successful runs retain timestamped raw sources, normalized CSV and hash manifest
under `data/processed/official_population/`. `latest.json` points to the latest
successful database import; dry runs write `latest_plan.json` instead. A failed
fetch or validation never replaces the successful manifest or writes population.
Official schemas, units, demographic totals, full province/state coverage,
positive counts and duplicate location/year keys are checked before import.
Re-running imports updates published revisions without allocating national totals.

NBS uses year-end resident population (China 2026 year-end is not published).
Brazil annual estimates reference 1 July; census counts reference 1 August and
are explicitly tagged as census observations, not annual estimates. Their bases
may create breaks in the series. Missing 2007/2023 Brazilian state denominators
stay absent. ABS uses June ERP where published, otherwise the latest observed
quarter within the same year (2026 currently 31 March).

WPP remains the national fallback for other countries and unfilled national
years; both WPP import paths now preserve existing official NBS/IBGE rows and
metadata. National WPP future-year values remain estimates/projections and must
not be described as observed NBS populations. The database still provisions WPP
for 43 active national locations (2000–2050).

Brazilian population-only states are registered with `location_type=subdivision`,
parent `BR`, and the official IBGE geography ID. The state-case adapter now uses official SINAN individual records, residence
`SG_UF` (or a consistent `ID_MN_RESI` municipality prefix) and notification month.
It never replaces residence with notifying `SG_UF_NOT`. States are activated for
site export only after successful disease import. Unknown/conflicting residence
and missing dates remain in audit counts, not assigned to a state or January.
Annual-source contributions to the same notification month are summed; only one
final/preliminary file per disease/year is selected. `NTRA` survey aggregates and
`SDTA` outbreak rows are excluded from the individual-case state adapter.

Daily scheduling templates are provided in
`deploy/systemd/globalid-population-refresh.service` and `.timer` (08:15
Asia/Shanghai, plus up to 30 minutes randomized delay). Each run discovers the
current official publication for CN/AU/BR/US/JP/SE: NBS indicator catalogue and explicit years through
the preceding calendar year, ABS latest release/workbook links, and every
published SIDRA estimate period plus census tables, Census download directories,
Japan annual and census-backcast catalogues, and SCB table metadata. No population CSV needs
manual downloading or maintenance. SIDRA's live state identifiers/names are
validated against the reviewed IBGE UF code crosswalk; an unrelated state-directory
API is not required. Historical official revisions are refreshed as well.

Sources have bounded HTTPS retries and independent validated imports. An IBGE
outage cannot block NBS or ABS; an invalid or partial source never changes that
source's imported data. The next daily check retries the unavailable source.
An unsuccessful service exits nonzero for the host's existing failure monitor.
The wrapper retains the last good source snapshot and writes
`data/processed/official_population/scheduler-status.json`: last attempt,
last success, consecutive failures, discovered official URLs, year coverage,
changed rows and publication retry state. Last successful check is distinct from
the population reference date: a successful check need not produce a new year.
Dry runs have a separate `scheduler-plan.json` and never replace applied receipts.

Changed values/basis/reference metadata create dirty scopes and request the
existing release queue once per batch. A busy or unavailable queue leaves a
pending retry for the next daily check, even if population values then remain
unchanged. The combined `latest.json` preserves successful earlier-source rows
when another source is unavailable. Per-source raw data, hashes and logs stay in
`data/processed/official_population/sources/` and `checks/`.

To install on another host, render the existing `__PROJECT_DIR__`,
`__RUN_AS_USER__`, `__RUN_AS_GROUP__` placeholders, install both units and enable
the timer. The six-source service has a four-hour timeout. This checks for publications daily; it never estimates an unpublished
year. It uses the existing data release pipeline rather than committing code.

The tracked CSV is a reviewable baseline, not the live update target. Runtime
refreshes use the database and timestamped manifests described above.

## Earlier retained snapshot

This snapshot supplies annual population for 31 mainland Chinese provinces and
8 Australian states/territories. It contains no allocation of national population,
interpolation, extrapolation, or carry-forward to missing years.

## Sources and coverage

- China: 2000–2024, 775 province/year rows, persons. NBS year-end resident
  population, converted from the official unit of 10,000 persons. Sources:
  [China Statistical Yearbook 2012 table 3-4](https://www.stats.gov.cn/sj/ndsj/2012/html/D0304C.xls)
  (2000–2001),
  [2014 table 2-5](https://www.stats.gov.cn/sj/ndsj/2014/zk/html/Z0205C.xls)
  (2002–2010),
  [2021 table 2-5](https://www.stats.gov.cn/sj/ndsj/2021/html/C02-05.jpg)
  (2011–2014), and
  [2025 table 2-5](https://www.stats.gov.cn/sj/ndsj/2025/html/C02-05.jpg)
  (2015–2024). Later editions take precedence for overlapping years. Source
  workbooks/images are retained here. The 2011–2019 transcription was checked
  against the downloadable 2021 workbook. Population revisions around censuses
  may cause breaks between editions. Provincial totals exclude active military;
  Hong Kong, Macao and Taiwan are not included in this mainland snapshot.
- Australia: 1981–2026, 368 state/year rows, persons, both sexes. Source:
  [ABS March 2026 release, quarterly population by sex and state/territory](https://www.abs.gov.au/statistics/people/population/national-state-and-territory-population/mar-2026).
  The original `310104.xlsx` workbook is retained. June ERP is used where
  available. For 2026, only the observed 31 March ERP is available; it is explicitly
  recorded as `latest_observed_quarter_same_year`, not a June or annual estimate.
  Recent ABS estimates are revisable.

## Import and refresh

```sh
venv/bin/python scripts/import_subdivision_population.py
venv/bin/python scripts/import_subdivision_population.py --apply
```

The default is a read-only plan. `--apply` upserts only these subdivision/year
rows into `population_records`, preserving source URL, reference date, basis,
and input SHA-256 in metadata. Unknown geography codes or duplicate rows fail
before writing. The coverage report is saved under
`data/processed/subdivision_population/import_report.json`.

The exporter joins population on the exact jurisdiction ID and calendar year,
then computes `cases / population * 100000` for each original reporting period.
Source markers are `nbs_computed`, `abs_computed`, `ibge_computed`, and
`wpp_computed` for the corresponding official denominator.
After refreshing website exports, run
`venv/bin/python scripts/verify_subdivision_incidence.py` to check every exported
NBS/ABS/IBGE rate against its exact location/year denominator. The audit is saved as
`data/processed/subdivision_population/rate_verification.json`.

Month/week rates are not annualized. NBS year-end and ABS June/March references
are different denominator conventions and must remain visible in provenance.
The earlier yearbook snapshot stops at 2024; the new public-API snapshot above
adds verified 2025 denominators. China 2026 provincial rates remain missing.

The earlier snapshot importer is retained for reproducibility only. Normal
updates use the scheduled official discovery above; they do not require manual
ABS workbook downloads or China CSV edits.

## Australian disease history

```sh
venv/bin/python scripts/fetch_au_subdivision_history.py --start-year 2000 --end-year 2026
venv/bin/python scripts/update_au_subdivisions.py \
  --archive-root data/raw/au_state_history/monthly --apply
```

Annual public NINDSS semantic queries retain state/month/disease grain and the
confirmed/probable case filter. The importer does not divide or sum national
counts to manufacture state observations. Public responses are archived without
credentials. Subtotals, schema drift, duplicate keys, query truncation and unknown
geographies are checked before import. Open months are excluded. Explicit zeroes
remain zero; the monthly dashboard's disclosure threshold (`<5`) remains a
suppressed/null observation, including when an annual grouping exposes an exact
small value. Suppressed cells are retained in CSVs and source observations, and
never converted to zero during legacy import. Existing national data is untouched.

## Brazil state cases and active timers

The initial 2026-10-06 import covers all 26 states and the Federal District,
2000-01 through 2026-09. It processes 1,065 selected annual extracts into
319,788 source state/category/month facts and 308,664 canonical disease/month
records. Coverage varies by disease. Notification records with no usable date
are excluded and audited; overlapping annual contributions are merged with all
source filenames retained. IBGE denominator gaps (2007 and 2023) remain missing.

```sh
# Full initial state history; retains source fingerprint caches
venv/bin/python scripts/update_br_subdivisions.py --apply --export
# Recent reporting-year revisions, including older annual-file contributions
venv/bin/python scripts/update_br_subdivisions.py --recent-years 2 --apply --export
systemctl status globalid-population-refresh.timer globalid-br-subdivisions.timer
```

Population checks run daily at 08:15 Asia/Shanghai plus up to 30 minutes
of randomized delay. State-case checks run Sundays at 07:45 Asia/Shanghai plus
up to 30 minutes. Both timers are enabled and active on this host. Services use
the same `flock` to serialize these two imports/exports and avoid writing their
site artifacts concurrently. Population changes enter the existing data release queue; state-case refreshes
retain their configured export policy. Neither task commits Git changes. Raw microdata is not copied into
frontend downloads; only state/month aggregates are exported.

The state adapter scans only non-identifying residence/date fields. It preserves
notification counts (not a claim that every notification is a confirmed case).
Explicit zero state cells are based on the full selected national microdata file
for an observed reporting month. Unknown residence is never allocated. Source
errors block the import, leaving earlier successful state observations intact.
Audit reports and aggregate CSVs are stored under
`data/processed/br/subdivisions/`; per-file caches under
`data/cache/br/state_aggregates/` are invalidated on source size/mtime changes.

Official disease source and field definitions:
[DATASUS transfer](https://datasus.saude.gov.br/transferencia-de-arquivos2/),
[SINAN residence fields dictionary](https://www.portalsinan.saude.gov.br/images/documentos/Agravos/Notificacao_Individual/DIC_DADOS_NET---Notificao-Individual_rev.pdf).

## Additional denominator conventions (US / JP / SE)

US denominators are official July 1 resident estimates. The latest supported
vintage is discovered dynamically because the Census API does not currently
serve the latest estimates. Newer decade releases supersede overlapping years;
the official 2000–2010 intercensal series is used as published, not interpolated
locally. AS/GU/MP/VI and an unpublished 2026 have no annual denominator here.

JP uses prefecture total population, all ages and both sexes, including foreign
residents. The official thousand-person unit is converted to persons without
claiming person-level precision. Official 2015/2020 census backcasts supersede
the original annual estimates for 2011–2014 and 2016–2019, with basis
`october_official_census_rebased_resident_population` and exact e-Stat file URLs.
The published annual/census-year tables supply the other years. Pending 2025
census and national monthly links do not become annual prefecture populations.
The discovery receipt records pending releases and applied official revisions.

SE uses 31 December residents for each county. Pre-2025 tables require all eight
mutually exclusive sex/marital-status cells; any missing cell fails validation.
2025+ CKM tables supply the official total directly, without adding perturbed
subtotals. `year_end_resident_population_ckm` explicitly identifies official
disclosure protection. The latest year advertised in the catalogue must appear
in the fetched table; historical-only responses cannot masquerade as fresh.

The daily service now checks six independent sources and has a four-hour outer
timeout for bounded per-source retries. Values/basis revisions automatically
mark their own geography dirty. Weekly map data retain source calendars (US
MMWR, JP IDWR ISO); monthly/quarterly/annual and custom date ranges only include
whole source periods. Cross-year cumulative report rates add each reporting
year's cases divided by that year's local population, multiplied by 100,000,
without annualization; annual contributions and both comparison windows' actual
coverage/provenance are included in the UI and CSV.
