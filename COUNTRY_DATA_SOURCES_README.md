# Country Surveillance Sources

Last reviewed: 2026-08-29

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
