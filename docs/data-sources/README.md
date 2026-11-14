# Country Surveillance Sources

Repository review: 2026-11-14. Source configuration was checked against
`configs/country_bootstrap.json` and the crawler implementations. This review
does not re-verify live collection, licences or public snapshots. The supported
source inventory below was previously reviewed on 2026-08-29; later national
adapters are listed separately with their repository evidence.

This document records the country and regional infectious-disease surveillance
source contracts used by GIDS and the public `/countries/` inventory. It distinguishes live data
pipelines from source-onboarding candidates. A country must not be presented as
active until its crawler, normalization, disease mapping, quality checks, and
database import have produced a verified site-data snapshot.

## Status definitions

- **Supported**: a working pipeline has imported records into GIDS and the
  public site has a country snapshot with real totals and coverage dates.
- **Scheduled**: an official source has been identified and is visible on the
  public roadmap, but no placeholder case data are published.
- **Regional baseline**: ECDC can provide a comparable annual history before a
  higher-frequency national source is implemented.
- **Source research**: the country remains on the roadmap, but the ingestion
  contract and official machine-readable source have not yet been selected.

## Supported countries and regions

| Code | Country or region | Primary official source | Cadence | Current ingestion pattern |
| --- | --- | --- | --- | --- |
| AU | Australia | [Australian NNDSS](https://www.health.gov.au/topics/national-notifiable-diseases-surveillance-system-nndss) | Monthly | Microsoft Power BI data aggregated to national totals. |
| BR | Brazil | [DATASUS / SINAN](http://siab.datasus.gov.br/DATASUS/index.php?acao=41&area=0901&item=1) | Monthly | Public DBC notification files from final and preliminary FTP collections. |
| CA | Canada | [Canadian Notifiable Disease Surveillance System](https://diseases.canada.ca/notifiable/extract-dataset) | Annual | National reported counts for 1924–2023 from the current PHAC CNDSS extract; the national aggregate is not an all-jurisdiction completeness claim because disease/year inclusion varies, including unavailable Manitoba data for 44 disease contracts in 2023. Null cells remain unknown and explicit zeroes are retained under the Open Government Licence – Canada. |
| CH | Switzerland | [FOPH/BAG IDD](https://www.idd.bag.admin.ch/en/portal-data) | Weekly | Mandatory-reporting REST data normalized to national case rows. |
| CN | China | [China CDC Weekly](https://weekly.chinacdc.cn) and [National Disease Control and Prevention Administration](https://www.ndcpa.gov.cn) | Monthly, with supplementary sources | National notifiable-disease reports plus supplementary official and literature discovery sources. |
| CZ | Czechia | [ECDC Surveillance Atlas](https://atlas.ecdc.europa.eu/public/index.aspx/) | Annual | Member-State reported closed-year counts; current-year partial cells are withheld. |
| ES | Spain | [ECDC Surveillance Atlas](https://atlas.ecdc.europa.eu/public/index.aspx/) | Annual | Member-State reported closed-year counts with unknown gaps and explicit zeroes preserved. |
| FR | France | [ECDC Surveillance Atlas](https://atlas.ecdc.europa.eu/public/index.aspx/) | Annual | Member-State reported country-level case counts from 1990 onward where published; explicit zeroes are preserved and absent topic/year cells remain unknown. |
| GR | Greece | [ECDC Surveillance Atlas](https://atlas.ecdc.europa.eu/public/index.aspx/) | Annual | ECDC `EL` source geography normalized to ISO `GR` while retaining source provenance. |
| HK | Hong Kong, China | [CHP Notifiable Infectious Diseases](https://www.chp.gov.hk/en/static/24012.html) | Monthly | Annual open-data CSV files normalized to national monthly totals. |
| JP | Japan | [JIHS Infectious Diseases Weekly Report](https://id-info.jihs.go.jp/en/surveillance/idwr/rapid/index.html) | Weekly | Weekly national total rows from the official IDWR publication. |
| IT | Italy | [ECDC Surveillance Atlas](https://atlas.ecdc.europa.eu/public/index.aspx/) | Annual | Member-State reported closed-year counts with source gaps retained as unknown. |
| KR | South Korea | [KDCA EID OpenAPI](https://www.data.go.kr/data/15139178/openapi.do) | Monthly | OpenAPI or official portal downloads aggregated to national totals. |
| NZ | New Zealand | [PHF Science Digital Library](https://www.phfscience.nz/digital-library/) | Monthly | Monthly notifiable-disease surveillance tables from PHF Science, formerly ESR. |
| PL | Poland | [ECDC Surveillance Atlas](https://atlas.ecdc.europa.eu/public/index.aspx/) | Annual | Member-State reported closed-year counts with asynchronous topic updates preserved. |
| PT | Portugal | [ECDC Surveillance Atlas](https://atlas.ecdc.europa.eu/public/index.aspx/) | Annual | Member-State reported closed-year counts with absent topics left unknown. |
| RO | Romania | [ECDC Surveillance Atlas](https://atlas.ecdc.europa.eu/public/index.aspx/) | Annual | Member-State reported closed-year counts with source availability retained verbatim. |
| SG | Singapore | [CDA Weekly Infectious Diseases Bulletin](https://www.cda.gov.sg/resources/weekly-infectious-diseases-bulletin-2026/) | Weekly | 2012–2022 official CSV history plus 2023+ CDA annual workbooks under operator-authorized public release. |
| TW | Taiwan, China | [CDC NIDSS](https://nidss.cdc.gov.tw/Home/Index) | Monthly | Open-data CSV files aggregated to national monthly totals. |
| US | United States | [CDC NNDSS](https://data.cdc.gov/browse?category=NNDSS) and [CDC NHSS](https://www.cdc.gov/hiv-data/nhss/) | Weekly and annual | Weekly NNDSS U.S.-resident national values; the broader published Total is retained as a separate source aggregate. Annual HIV diagnoses come from NHSS. |

## Supported ECDC regional baselines

The [ECDC Surveillance Atlas of Infectious Diseases](https://atlas.ecdc.europa.eu/public/index.aspx/)
supports CSV exports of aggregate EU/EEA surveillance data. A shared ECDC
crawler now establishes annual historical baselines for all 30 EU/EEA countries
and a historical United Kingdom baseline:

| Code | Country | Initial source | Cadence |
| --- | --- | --- | --- |
| AT | Austria | ECDC Surveillance Atlas | Annual |
| BE | Belgium | ECDC Surveillance Atlas | Annual |
| BG | Bulgaria | ECDC Surveillance Atlas | Annual |
| HR | Croatia | ECDC Surveillance Atlas | Annual |
| CY | Cyprus | ECDC Surveillance Atlas | Annual |
| CZ | Czechia | ECDC Surveillance Atlas | Annual |
| DK | Denmark | ECDC Surveillance Atlas | Annual |
| EE | Estonia | ECDC Surveillance Atlas | Annual |
| FI | Finland | ECDC Surveillance Atlas | Annual |
| FR | France | ECDC Surveillance Atlas | Annual |
| DE | Germany | ECDC Surveillance Atlas | Annual |
| GR | Greece | ECDC Surveillance Atlas | Annual |
| HU | Hungary | ECDC Surveillance Atlas | Annual |
| IS | Iceland | ECDC Surveillance Atlas | Annual |
| IE | Ireland | ECDC Surveillance Atlas | Annual |
| IT | Italy | ECDC Surveillance Atlas | Annual |
| LV | Latvia | ECDC Surveillance Atlas | Annual |
| LI | Liechtenstein | ECDC Surveillance Atlas | Annual |
| LT | Lithuania | ECDC Surveillance Atlas | Annual |
| LU | Luxembourg | ECDC Surveillance Atlas | Annual |
| MT | Malta | ECDC Surveillance Atlas | Annual |
| NL | Netherlands | ECDC Surveillance Atlas | Annual |
| NO | Norway | ECDC Surveillance Atlas | Annual |
| PL | Poland | ECDC Surveillance Atlas | Annual |
| PT | Portugal | ECDC Surveillance Atlas | Annual |
| RO | Romania | ECDC Surveillance Atlas | Annual |
| SK | Slovakia | ECDC Surveillance Atlas | Annual |
| SI | Slovenia | ECDC Surveillance Atlas | Annual |
| ES | Spain | ECDC Surveillance Atlas | Annual |
| SE | Sweden | ECDC Surveillance Atlas | Annual |
| GB | United Kingdom | ECDC Surveillance Atlas historical baseline (`UK` upstream) | Annual |

All countries above use the shared contract as supported public annual baselines.
Current-year cells are withheld until the calendar
year closes, and a refreshed history window replaces withdrawn source cells
instead of retaining stale values.
ECDC data should be treated as a historical baseline or fallback source. It
normally has more reporting delay than national surveillance systems. When a
national source is added, both sources must have distinct canonical source keys
and an explicit primary/fallback policy so overlapping records are not counted
twice.
The United Kingdom series is historical and is canonicalized from the ECDC
source geography `UK` to platform code `GB`. England-only UKHSA laboratory
notifications are not treated as United Kingdom national case counts.

## Additional roadmap entry

| Code | Country | Status | Next action |
| --- | --- | --- | --- |
| TH | Thailand | Source research | DOE DDS public Tableau exports expose only a current-week KPI or limited priority-disease snapshot; the national export and zero-reporting systems require login, and no explicit public reuse licence was found. Keep unpublished until a stable national API defines revisions, zeroes, missingness, and coverage. |

## Onboarding requirements

For every scheduled country:

1. Confirm the official source, licence or reuse terms, geographic scope, case
   definition, reporting cadence, and revision policy.
2. Preserve the source publication date and distinguish an explicit zero from
   missing or suppressed data.
3. Normalize to national totals without mixing sentinel counts, laboratory
   detections, notifications, and estimated infections.
4. Assign one canonical source key and keep regional baselines separate from
   national primary feeds.
5. Add disease mappings, raw-source archives, incremental revision handling,
   data-quality checks, and end-to-end tests.
6. Change the public status from **Scheduled** to **Supported** only after real
   database records and a generated country snapshot are available.

## Additional national and provincial adapters

These adapters are present in the current repository. A configured adapter is
not by itself proof of a successful production import or public release.
National feeds and ECDC baselines retain separate source identities.

| Geography | Official source | Source scope | Stored cadence |
| --- | --- | --- | --- |
| AT | [AGES Radar](https://www.ages.at/en/human/disease/ages-radar-for-infectious-diseases) | `ages_radar` | Monthly |
| DE | [RKI SurvStat](https://survstat.rki.de/Content/Query/Create.aspx) | `rki_survstat` | Weekly |
| FI | [THL infectious-disease register](https://sampo.thl.fi/pivot/prod/en/ttr/cases/fact_ttr_cases) | `thl_ttr` | Monthly |
| IE | [HPSC](https://www.hpsc.ie/notifiablediseases/annualidstatistics/) | `hpsc_ndh`, `hpsc_weekly_archive`, `hpsc_annual` | Weekly and annual; see release conditions below |
| IS | [Directorate of Health](https://island.is/en/smitsjukdomar-tolur) | `is_doh_annual`, `is_doh_sti`, `is_doh_respiratory`, `is_doh_history`, `is_doh_legacy_icd` | Annual, monthly and weekly by source |
| NO | [FHI MSIS](https://allvis.fhi.no/msis/sykdomshendelser) | `fhi_msis` | Monthly |
| SE | [Folkhälsomyndigheten](https://www.folkhalsomyndigheten.se/statistik-och-data/hitta-statistik-och-data/smittsamma-sjukdomar-statistik/) | `fohm_sminet` | Monthly |
| CA-ON | [Public Health Ontario](https://www.publichealthontario.ca/en/data-and-analysis/infectious-disease/reportable-disease-trends-annually) | `pho_idto_monthly` | Monthly; Ontario only |

## Switzerland source and geography

The [FOPH/BAG IDD portal](https://www.idd.bag.admin.ch/en/portal-data) uses
`https://www.idd.bag.admin.ch/api/v1`, with `/data/version`, `/data/sets`,
`POST /data/{disease}/cases/value/{period}` and corresponding details endpoints.
The canonical source scope is `foph_idd`. Periods are monthly, ISO weekly or
annual according to each disease's source contract.

Use `CH` where the source exposes Switzerland alone. Combined Switzerland and
Liechtenstein observations remain `CHFL`; they must not be relabelled as Swiss
national facts. The historical repair tool
[`migrate_chfl_geography.py`](../../scripts/maintenance/migrate_chfl_geography.py)
audits by default and preserves observed values and revision history.

```bash
# Normal task-based collection; requires a running worker.
venv/bin/python main.py crawl --country CH --source foph_idd --process --save-raw

# Historical compatibility import, only for an explicit migration/recovery need.
venv/bin/python scripts/import_ch_history.py --start-year 2013
```

The historical importer is a legacy compatibility path; source-series facts
and current ingestion use the v3 contracts. The configured rolling refresh
covers six months, twelve weeks and two years as applicable; forced historical
refresh uses `--force --start-year 2013`. Retain geographic and revision semantics.

## Ireland release conditions

On 2026-11-09 the site operator directed that the Ireland HPSC feeds be
published before written permission was obtained, with the status disclosed on
the website. This is an operator decision, not evidence that HPSC granted
permission. The source metadata retains `written_permission_required`; rights
for the reconstructed weekly archive have not been verified.

The country page and source cards identify the HPSC current weekly hub,
historical weekly archive, historical annual reports and ECDC annual baseline
separately. ECDC's own attribution remains attached to the ECDC source. The
sources keep their original weekly or annual granularity; missing archive weeks
are not zero. Legacy compatibility records remain excluded from public export,
and the source allow-list admits only the four named Ireland source systems.

The operator decision is recorded by `operator_authorized_public_release` in
the IE crawler configuration. Removing it makes collection refuse to use an
updater that declares public release enabled. Revoking public publication also
requires closing the country and per-source release gates and running the
normal publication-retirement workflow.

## Related source guides

- [Add a country](ADD_NEW_COUNTRY_GUIDE.md)
- [China provinces](CN_PROVINCE_DATA.md)
- [Canada high-frequency sources](CA_HIGH_FREQUENCY_SURVEILLANCE_PLAN.md)
- [Iceland integration and replay](ICELAND_INTEGRATION_REVIEW.md)
- [Subnational roadmap](SUBNATIONAL_DATA_ROADMAP.md)
- [Provisional and revisable observations](PROVISIONAL_SURVEILLANCE_AUDIT.md)
- [Situation authority coverage](SITUATION_SOURCE_COVERAGE.md)
- [Parsers](PARSER.md)
