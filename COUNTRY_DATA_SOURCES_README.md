# Country Surveillance Sources

Last reviewed: 2026-08-04

This document records the country and regional infectious-disease surveillance
sources represented on the GIDS `/countries/` page. It distinguishes live data
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
| CH | Switzerland | [FOPH/BAG IDD](https://www.idd.bag.admin.ch/en/portal-data) | Weekly | Mandatory-reporting REST data normalized to national case rows. |
| CN | China | [China CDC Weekly](https://weekly.chinacdc.cn) and [National Disease Control and Prevention Administration](https://www.ndcpa.gov.cn) | Monthly, with supplementary sources | National notifiable-disease reports plus supplementary official and literature discovery sources. |
| HK | Hong Kong, China | [CHP Notifiable Infectious Diseases](https://www.chp.gov.hk/en/static/24012.html) | Monthly | Annual open-data CSV files normalized to national monthly totals. |
| JP | Japan | [JIHS Infectious Diseases Weekly Report](https://id-info.jihs.go.jp/en/surveillance/idwr/rapid/index.html) | Weekly | Weekly national total rows from the official IDWR publication. |
| KR | South Korea | [KDCA EID OpenAPI](https://www.data.go.kr/data/15139178/openapi.do) | Monthly | OpenAPI or official portal downloads aggregated to national totals. |
| NZ | New Zealand | [PHF Science Digital Library](https://www.phfscience.nz/digital-library/) | Monthly | Monthly notifiable-disease surveillance tables from PHF Science, formerly ESR. |
| TW | Taiwan, China | [CDC NIDSS](https://nidss.cdc.gov.tw/Home/Index) | Monthly | Open-data CSV files aggregated to national monthly totals. |
| US | United States | [CDC NNDSS](https://data.cdc.gov/browse?category=NNDSS) and [CDC NHSS](https://www.cdc.gov/hiv-data/nhss/) | Weekly and annual | Weekly NNDSS U.S.-resident national values; the broader published Total is retained as a separate source aggregate. Annual HIV diagnoses come from NHSS. |

## Scheduled national-source onboarding

These sources are recommended for direct national ingestion. The order below is
also the proposed implementation sequence.

| Priority | Code | Country or area | Official source | Cadence | Integration notes |
| --- | --- | --- | --- | --- | --- |
| 1 | SG | Singapore | [CDA Weekly Infectious Diseases Bulletin](https://www.cda.gov.sg/resources/weekly-infectious-diseases-bulletin-2026/) | Weekly | Multi-disease bulletins with a yearly XLSX download and weekly PDFs. This is the strongest next onboarding candidate. |
| 2 | AT | Austria | [AGES Radar for Infectious Diseases](https://www.ages.at/en/human/disease/ages-radar-for-infectious-diseases/) | Monthly | Monthly statutory-notification tables include downloadable CSV data. Archive each issue and retain the reported reference date. |
| 3 | IE | Ireland | [HPSC National Notifiable Disease Hub](https://notifiabledisease.hpsc.ie/) | Weekly | Covers roughly 80 notifiable diseases. Inspect the ArcGIS service for stable aggregate-data downloads. |
| 4 | SE | Sweden | [Official notifiable-disease statistics](https://www.folkhalsomyndigheten.se/statistik-och-data/hitta-statistik-och-data/smittsamma-sjukdomar-statistik/) and [Folkhalsodata PxWeb](https://fohm-app.folkhalsomyndigheten.se/Folkhalsodata/pxweb/en/) | Monthly | PxWeb supports machine queries for cases, rates, time, and geography. |
| 5 | NO | Norway | [FHI MSIS Statistics Bank](https://allvis.fhi.no/msis) | Daily | Open statistics cover 1977 to the present. Discover and document the Allvis query endpoint before automating. |
| 6 | DK | Denmark | [SSI Surveillance Statistics](https://statistik.ssi.dk/) | Weekdays | Individually notified disease data are counted on working days. The dynamic query interface requires endpoint discovery. |
| 7 | FI | Finland | [THL Infectious Diseases Register](https://sampo.thl.fi/pivot/prod/en/ttr/cases/fact_ttr_cases) | Periodic | Multi-disease register statistics extend back to 1995. Use the THL cube export or query interface. |
| 8 | CA | Canada | [Canadian Notifiable Disease Surveillance System](https://diseases.canada.ca/notifiable/charts-list?wbdisable=true) | Annual | National counts and rates can be exported in large CSV or Excel extracts; some histories begin in 1924. |
| 9 | DE | Germany | [RKI SurvStat](https://survstat.rki.de/) | Current aggregate | Rich custom queries for notifiable cases and pathogens. ASP.NET session handling and transient service errors make automation more complex. |
| 10 | GB | England and Wales | [UKHSA notifiable causative-agent reports](https://www.gov.uk/government/publications/notifiable-diseases-causative-agents-reports-for-2026) | Weekly | The current causative-agent series is available as weekly HTML. It must be labelled England and Wales rather than the whole United Kingdom. The separate clinical NOIDs weekly publication has been paused since April 2025. |
| 11 | NL | Netherlands | [RIVM infectious-disease notifications](https://www.rivm.nl/meldingsplicht-infectieziekten/overzicht-meldingen) | Annual | A simple official HTML table provides annual notification totals from 2016 onward. |
| 12 | IS | Iceland | [Directorate of Health infectious-disease statistics](https://island.is/en/smitsjukdomar-tolur) | Mixed | Annual multi-disease data are supplemented by quarterly STI and seasonal weekly respiratory surveillance dashboards. |

## ECDC regional baseline candidates

The [ECDC Surveillance Atlas of Infectious Diseases](https://atlas.ecdc.europa.eu/public/index.aspx/)
supports CSV exports of aggregate EU/EEA surveillance data. A shared ECDC
crawler can establish annual historical baselines for the following roadmap
countries before national feeds are implemented:

| Code | Country | Initial source | Cadence |
| --- | --- | --- | --- |
| FR | France | ECDC Surveillance Atlas | Annual |
| ES | Spain | ECDC Surveillance Atlas | Annual |
| IT | Italy | ECDC Surveillance Atlas | Annual |
| PT | Portugal | ECDC Surveillance Atlas | Annual |
| PL | Poland | ECDC Surveillance Atlas | Annual |
| CZ | Czechia | ECDC Surveillance Atlas | Annual |
| GR | Greece | ECDC Surveillance Atlas | Annual |
| RO | Romania | ECDC Surveillance Atlas | Annual |

ECDC data should be treated as a historical baseline or fallback source. It
normally has more reporting delay than national surveillance systems. When a
national source is added, both sources must have distinct canonical source keys
and an explicit primary/fallback policy so overlapping records are not counted
twice.

## Additional roadmap entry

| Code | Country | Status | Next action |
| --- | --- | --- | --- |
| TH | Thailand | Source research | Select a stable official national surveillance source, confirm reuse conditions, and define the reporting cadence before implementation. |

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
