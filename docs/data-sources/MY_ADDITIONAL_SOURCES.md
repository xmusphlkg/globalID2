# Malaysia additional infectious-disease sources

Verified on 2026-11-16 by anonymous requests to the original official sites.
These contracts supplement the existing five-category MOH STD annual dataset.
No source here is a replacement for complete Malaysian national notifiable
case surveillance; source type and denominators must remain visible.

## WHO FluNet: current weekly laboratory surveillance

[WHO's official FluNet page](https://www.who.int/tools/flunet) explicitly links
its [public CSV/OData service](https://xmart-api-public.who.int/FLUMART/VIW_FNT?$format=csv).
The actual endpoint has **no `/api` path component**. The reviewed Malaysia
query is `https://xmart-api-public.who.int/FLUMART/VIW_FNT?$filter=COUNTRY_CODE%20eq%20%27MYS%27`.
It returned HTTP 200, 1,807 records, no next-page link, covering 1996–2026.
The latest observed ISO week is 2026-W43, explicit source week-start
2026-10-19. That week reports 233 influenza-positive samples (182 A, 51 B),
924 influenza specimens processed, five RSV specimens processed and zero RSV
positives. These have different denominators. The latest data are several
weeks behind the investigation date; this is weekly reporting, not live cases.

The original rows include SENTINEL/non-sentinel/unspecified surveillance origin,
influenza subtypes, influenza totals, RSV, adenovirus, bocavirus, human
coronavirus, metapneumovirus, parainfluenza and rhinovirus. Only observed,
nonmissing values can be observations. Preserve source origin and pathogen
field identity; totals and their subtypes overlap and are never additive.
Human coronavirus is source-native and must not be relabelled COVID-19.
Counts describe laboratory detections in reporting networks, not all national
patients. ILI_ACTIVITY is an ordinal activity code, not a count.

Implemented as `my_who_flunet.py`, scope `who_flunet_weekly`, ontology source
`SRC_MY_WHO_FLUNET`, updater `MYWHOFluNetUpdater`. The full-history import
stored 19,722 observations across 21 detection fields. The source dictionary
defines 25 eligible fields; 25,453 null detection cells remain absent rather
than becoming zero. All original rows and null cells remain in raw artifacts.
Raw HTTP pages, source-provided ISO dates, specimen denominators, comments,
surveillance origin, source record index and SHA-256 remain auditable.
Surveillance origin participates in the observation identity. ISO year, week
and Monday start must agree; open weeks are withheld using the captured
retrieval date, including during offline replay.
The quality report retained three warning-only findings: 137 incoming inferred
missing periods, 552 periods with fewer reporting detection fields, and 2,924
projected inferred gaps across 18 series. These frequency-based checks include
source-null fields and changing surveillance origins. Full raw-to-database
replay confirmed every available value; these warnings do not justify filling
source omissions or merging origins.

## WHO GHO: reported national annual counts with long history

The anonymous [WHO GHO API](https://ghoapi.azureedge.net/api/) returned actual
Malaysia (`SpatialDim=MYS`) observations without pagination for:

| Source indicator | Meaning | Verified years | Latest value / source update |
| --- | --- | --- | --- |
| `WHS3_62` | Measles reported cases | 1974–2025 | 917 in 2025; 2026-07-14 |
| `TB_c_newinc` | Tuberculosis new and relapse notifications | 2000–2025 | 26,672 in 2025; 2026-11-11 |
| `WHS3_45` | New leprosy cases | 2005–2025 | 381 in 2025; 2026-09-14 |
| `MALARIA_CONF_CASES` | Confirmed malaria cases | 2015–2024 | 2,384 in 2024; 2025-12-19 |

Actual query example:
`https://ghoapi.azureedge.net/api/WHS3_62?$filter=SpatialDim%20eq%20%27MYS%27`.
Use NumericValue and the indicator metadata; where legacy cholera records have
null NumericValue but an explicit integer Value, retain that reported integer.
Keep every indicator definition separate. Country-reported cases and notifications are eligible; modelled
incidence, population rates, treatment coverage and prevalence estimates are
separate statistical quantities and are excluded. Reported year and source
update date differ. A daily revision check still yields annual observations.

Implemented as `my_who_annual.py`, scope `who_gho_annual`, ontology source
`SRC_MY_WHO_GHO`, updater `MYWHOAnnualUpdater`. Eighteen reviewed indicators
were queried; fifteen have Malaysia records, covering 1954–2025. These include
cholera cases and deaths, diphtheria, Japanese encephalitis, pertussis, new
leprosy cases, total tetanus, poliomyelitis, mumps, congenital rubella syndrome,
neonatal tetanus, rubella, measles, tuberculosis new/relapse notifications and
confirmed malaria. These are fifteen indicators, not mutually exclusive
disease groups. Total/neonatal tetanus and cases/deaths retain separate series.

The import stored 553 observations: 493 numeric counts and 60 explicit source
missing values stored as NULL. Meningitis (`WHS3_47`), H5N1 (`WHS3_51`) and
malaria deaths (`MALARIA001`) have no Malaysia rows, which is not zero cases.
Twenty-seven absent historical cholera annual periods generated warning-only
gap findings; source gaps are retained without interpolation. Annual periods
must be closed as of the captured retrieval date. Estimated bounds, unexpected
country/strata, changed indicator labels and invalid counts fail validation.

## Completed import and update contract

Both source pipelines capture original HTTP pages before parsing, follow and
validate every OData page, retain hashes and provenance, and can replay offline.
They refresh all published history to capture revisions. Quality guards are
explicitly fail-closed. Original source series remain internal holding series;
laboratory detections are not projected into national patient case counts.
Mapping notifications and public release are disabled.

| Scope | Task | Crawl run | Imported observations |
| --- | --- | --- | --- |
| `who_flunet_weekly` | `21615cd2-a274-4b56-9d3a-67915389b815` | 31004 | 19,722 |
| `who_gho_annual` | `81bbcfc9-8097-473e-9fba-6c229b889bb1` | 31005 | 553 |

The completed result and source audits are in
`reports/onboarding/my_who_import_20261116.json`. Read-only database verification
in `reports/onboarding/my_who_database_20261116.json` confirmed every stored
value, dimension and raw provenance field exactly matches offline artifact
replay, all artifact checksums match, all six stages completed, and no duplicate
natural keys or mapping outbox events exist for either source. FluNet retains
12,318 reported zeros and GHO retains 54 reported zeros separately from missing
data. FluNet has 2,916 field/week pairs with multiple origins safely held as
distinct observations. The reproducible foreground
import command is `venv/bin/python scripts/import_my_who_sources.py`; optional
`--sources` selects either scope and `--start-year` limits the historical import.
The normal default covers the complete published history. This script uses
the staged ingestion service and a renewed foreground task lease.

## Other sources and unresolved coverage

### Follow-up review: three current-source routes

The following additional read-only checks were completed on 2026-11-16. No
new parser, scheduled acquisition or database import was implemented in this
follow-up.

**1. Selangor 2025 annual report: verified state multi-disease candidate.**
The [official Selangor health-department homepage](https://jknselangor.moh.gov.my/)
returned HTTP 200 and explicitly links
[Laporan Tahunan 2025](https://heyzine.com/flip-book/618709c75f.html).
The public viewer returned HTTP 200 and identifies a 323-page report. Its
[underlying public PDF](https://cdnm.heyzine.com/files/uploaded/v3/618709c75f72be5b6fb5ae14978385cc1262ad18.pdf)
also returned HTTP 200, `application/pdf`, 56,885,480 bytes, SHA-256
`debab948e5163c60d943412fa1aac863ba7f3c60040274d12d504399c181864e`.
Original text and rendered pages were both checked. This is **Selangor state**
and district data for 2025, not a Malaysia national feed or 2026 observations.
The homepage's 2026 update timestamp does not change the reporting period.

PDF page 92 (printed page 82) has a fifteen-disease registered-case table,
including HFMD 65,535, tuberculosis 5,642, malaria 156, measles 115 and
leptospirosis 93. These are source quotations, not approved import values:
other chapters in the same report give HFMD 73,785 (PDF page 90), TB 5,767
(page 98), and confirmed measles 116 (page 86). The measles prose also labels
16.1 as per 100,000 whereas its graph labels incidence per 1,000,000.
These conflicts cannot be silently reconciled, averaged or overwritten.
Any eventual parser must preserve chapter/table identity and hold conflicting
definitions for review before selecting a comparable annual series.

Leptospirosis 93 is consistent between the narrative, overall table and
nine-district table on PDF pages 91–92. The malaria district table on page 107
provides cases and deaths for 2023, 2024 and 2025 (state totals 149/3, 115/1,
156/0 respectively). Malaria 156 includes 56 zoonotic and 100 human cases;
the report's zero indigenous human malaria indicator is a distinct subset,
not zero total malaria. Other chapters show historical charts (measles
2019–2025, TB 2015–2025); chart years do not constitute a validated machine
readable history.

For future acquisition, monitoring the official homepage's annual-report
anchor could discover a newly linked viewer; that viewer's public metadata
currently exposes the PDF filename. There is no verified fixed annual API,
stable year-derived PDF URL, guaranteed release date or automatic complete
archive. The current PDF is reachable without login, but no parser or update
job has been commissioned. The separately linked 2024 AnyFlip viewer returned
HTTP 403, so a complete multi-year backfill is not yet verified.

**2. WHO WPRO HFMD: explicitly discontinued historical archive.**
The [official HFMD surveillance archive](https://www.who.int/westernpacific/emergencies/surveillance/archives/hand-foot-and-mouth-disease)
returned HTTP 200. It explicitly states that WHO WPRO “will no longer produce
the bi-weekly Western Pacific Region Hand, Foot and Mouth Disease Surveillance
Summary.” Its listed situation-update archives are 2015–2018. It is a
historical reference, not an eligible current updater; no Malaysian current
period or complete national time series was verified from it.

**3. National MOH publication/open-data routes: no new working feed verified.**
The official [healthcare catalogue](https://data.gov.my/data-catalogue?category=healthcare)
returned HTTP 200. Its infectious-disease entries still expose the existing
STD-by-state dataset (through 2024) and COVID datasets (through 2025-05-31),
not an HFMD/leptospirosis/TB/measles/malaria national feed. The official
[KKMNOW repository tree](https://api.github.com/repos/MoH-Malaysia/kkmnow-data/git/trees/main?recursive=1)
also returned HTTP 200 but contained no separate HFMD/leptospirosis/TB/measles/
malaria dataset. The catalogue's linked legacy portal, `archive.data.gov.my`,
failed TLS handshakes for both tested CKAN API path variants; it cannot yet be
treated as a functioning discovery/download route.

The [MOH media route](https://www.moh.gov.my/kenyataan-media) and tested MOH
database-store publication route returned HTTP 403. The Director-General blog
homepage/search returned HTTP 403; its tested WordPress HFMD/leptospirosis
search endpoints returned an empty collection (one initial request returned
503), and the WordPress.com API returned `unknown_blog`. No actual report was
obtained through these paths. These reachability failures and empty search
results say nothing about disease incidence. No new current **national**
HFMD/leptospirosis feed was established; the already implemented WHO annual
TB/measles/malaria sources remain the verified national supplementary routes.

Raw responses, PDF, extracted text and page checks for this review are under
`/tmp/my_source_discovery_20261116/current_candidates/`; its summarized evidence
is `reports/onboarding/my_current_source_candidates_20261116.json`.

- iDengue is a separately implemented candidate owned by another task; its
  verified daily dengue data are not duplicated here.
- KKMNOW's COVID dashboard and original COVID CSV stop at 2025-05-31. The
  public data.gov.my infectious-disease catalogue still contains those COVID
  datasets and STD annual data. A recent repository push is not fresh cases.
- The MOH main site/Health Facts guessed paths remain HTTP 403 in this
  environment. They have not been declared working acquisition routes.
- Selangor, Sabah, Johor and Penang health-department homepages are reachable;
  their publications are state data unless their original table explicitly
  identifies Malaysia. They must not be summed or relabelled national counts.
- [Sabah's official publication catalogue](https://jknsabah.moh.gov.my/jkns/penerbitan/penerbitan-dan-laporan)
  links [“Health Fact 2019”](https://jknsabah.moh.gov.my/jkns/dokumen/kesihatan-awam/laporan-2/42-health-fact-2019).
  The verified download is a one-page PDF (657,280 bytes), titled “HEALTH FACTS
  SABAH 2019”. Its communicable-disease table explicitly reports Sabah total
  cases and incidence rates, including HFMD 6,595, leptospirosis 128 and TB
  5,255. This is a historical state candidate, not an implemented national
  feed. The dense multicolumn layout needs a separate reviewed parser.
- Current national HFMD, leptospirosis and complete national notifiable-disease
  coverage remain gaps. The verified Selangor annual report above only covers
  that state, and has unresolved within-report conflicts. No
  unavailable source is represented as zero data.

Discovery bytes and intermediate evidence are held under
`/tmp/my_source_discovery_20261116/`. Source implementations remain internal,
with mapping notifications and public release disabled.
