# Netherlands RIVM weekly surveillance

The two independent source contracts below complement the existing annual
Osiris-AIZ notifications. They retain every eligible native measure; no COVID-only
selection substitutes for broader surveillance. Public release and mapping email
notifications are disabled for both contracts.

| Scope | Source ID | Source contract |
| --- | --- | --- |
| `rivm_virological_weekly` | `SRC_NL_RIVM_VIROLOGICAL` | Participating laboratories' weekly detected infections |
| `rivm_respiratory_weekly` | `SRC_NL_RIVM_RESPIRATORY` | Separate RIVM respiratory surveillance resources |

## Virological weekly returns

The [official explanation](https://www.rivm.nl/virologische-weekstaten) links to
the [public CSV](https://www.rivm.nl/sites/default/files/2019-01/RecenteVirUitslagen27w.csv).
The reviewed 2026-11-15 release has 49 source categories across 27 explicit
`JaarWeek` columns, 202619–202645, producing 1,323 observations. All categories
are retained, including combined, untyped and "other" groups. They cannot be
summed into independent disease totals.

These counts are laboratory detections, not nationwide approved notification
incidence. Each row retains its source week's `AantalLabs` denominator; the
latest week had 18 reporting laboratories. The denominator is provenance,
not a second patient count. Source blank cells remain unknown, explicit zeroes
remain observations and fractional or negative detection counts fail closed.

The source is a rolling public window. Each refresh rereads that complete
window and upserts revisions. Previously collected older facts remain in the
database after they leave the window. The current endpoint does not establish
availability of a complete historical archive.

## Respiratory surveillance package

The [current surveillance page](https://www.rivm.nl/luchtweginfecties/actueel)
publishes weekly figures. The [public metadata API](https://data.rivm.nl/opendata/api/3/action/package_show?id=res-ari-surveillance)
is used to discover current download URLs, which contain release timestamps.
Each collection captures the complete metadata response, every CSV and JSON
resource, and the landing page. Organization is an offline replay of those
integrity-checked bytes.

The reviewed 2026-11-13 package has 23 CSVs and 23 JSON resources. Seventeen
CSVs have explicit `season`, `week` and `value` fields and verified chart
bindings, populations and units:

- Infectieradar symptom and positive self-test percentages;
- NICE acute respiratory infection ICU admissions;
- Nivel primary-care consultation incidence for acute respiratory infection,
  pneumonia, and bronchiolitis in children age 0–4;
- pediatric ICU RSV admissions;
- national SARS-CoV-2 wastewater load, in the original scale of 100 billion
  particles per 100,000 inhabitants;
- Infectieradar specimen SARS-CoV-2 positivity;
- separate NWKV detection counts and positive-specimen percentages for
  influenza A, influenza B, RSV and SARS-CoV-2.

Each resource has a distinct stable source category and definition, so counts,
percentages, consultation incidence, specimen populations and ICU populations
cannot merge. The published chart's `dataSrc` must name the captured CSV's
exact resource ID, and its unit and explicit season grouping are checked.
The Nivel ARI definition records the incident-case methodology published from
2026-09-02; the current release's historical values retain that publication
contract rather than mixing them with an older methodology.

Six CSVs contain only week numbers, with no source year or season. Their
published chart configurations also lack a year/season context. They are
captured and listed as excluded in `source_audit`, rather than assigned guessed
years: two Infectieradar multi-pathogen tables, the NIC influenza subtype table,
and three Nivel specimen tables (ILI, other ARI and total ARI). Their raw bytes
remain available for a later explicit calendar-context upgrade.

## Source periods and storage dates

No verified official week-boundary calendar was found in the captured CSVs,
chart configurations, RIVM explanation or the Nivel methods page inspected.
**January 1 timestamps are storage anchors, not actual weekly dates.**

- Virological observations retain explicit source year/week dimensions.
- Respiratory observations retain explicit source season/week dimensions. The
  first year of the season string supplies only the storage anchor; it is not
  claimed to be the observation's calendar year.
- `CalendarStatus`, `DateRole` and `TimeBasis` make this limitation explicit;
  `PeriodStart`/`PeriodEnd` remain empty. No ISO boundaries, daily values or
  missing-week zeroes are invented.

The natural observation key includes these source dimensions, preventing week
collisions under an annual anchor. Use the original source period labels for
inspection. A future verified calendar requires an explicit identity migration;
changing `Date` during a normal refresh would create duplicate facts. These
contracts remain internal and cannot supply a public weekly-date plot as-is.

## Running and validation

```bash
venv/bin/python scripts/import_nl_weekly.py --source rivm_virological_weekly
venv/bin/python scripts/import_nl_weekly.py --source rivm_respiratory_weekly
venv/bin/python -m pytest tests/unit/test_nl_weekly.py -q
```

The foreground importer uses the normal durable collect/organize/store pipeline
and a renewing task lease. `--replay-task-uuid` imports captured bytes under a
new task without downloading them again. Updaters ignore monthly planning
filters, which cannot safely filter year anchors. Refreshes are upserts;
withdrawn source cells do not automatically authorize database deletion.

Implementation: `src/data/crawlers/nl_weekly.py`,
`src/data/processors/nl_weekly.py`,
`src/services/ingestion/countries/nl_weekly.py`.

## Verified imports on 2026-11-16

- Virological task `291d8405-ad77-4242-89c8-d927a64da2b9`, CrawlRun `31002`:
  1,323 stored observations across 49 source holding series; two immutable
  artifacts; no quality issues.
- Respiratory task `25bf9de2-c361-473a-970b-6524ae5e5f0f`, CrawlRun `31003`:
  3,033 stored observations across 17 distinct source holding series; 48
  immutable artifacts; no quality issues. The captured package includes all
  23 CSVs, with the six undated resources explicitly excluded as described above.

Result ledgers are `data/current/nl/rivm_virological_weekly_import_result.json`
and `data/current/nl/rivm_respiratory_weekly_import_result.json`. Independent
offline raw-to-database verification is recorded in
`reports/onboarding/nl_weekly_review_20261116.json`.
