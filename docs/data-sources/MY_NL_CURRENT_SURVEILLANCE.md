# Current surveillance integration for Malaysia and the Netherlands

Verified against public official endpoints on 2026-11-16. Five new source
connectors have now completed actual database imports. Source-specific
contracts and evidence are documented in [NL weekly surveillance](NL_WEEKLY_SURVEILLANCE.md),
[MY daily dengue](MY_IDENGUE_DAILY.md), and [MY/WHO sources](MY_ADDITIONAL_SOURCES.md).
The [automation contract](NATIONAL_NOTIFICATION_AUTOMATION.md) lists persistent
publication checks. All five new jobs also completed their first real
scheduler/worker runs, with source bytes captured and observations stored;
they were initially commissioned as daily jobs; all eleven TH/MY/NL jobs now use 30-minute checks
under the same control-panel job IDs (`interval_minutes=30`, `daily_time=null`).
The interval change uses the
existing scheduler, queue and worker controls; source data remains daily,
weekly or annual according to its publication contract. See the initial
[commissioning ledger](../../reports/onboarding/my_nl_commissioning_20261116.json)
and [runtime audit](../../reports/onboarding/my_nl_current_runtime_20261116.json).

## Existing ingestion

| Country/source | Stored coverage | Refresh interpretation |
| --- | --- | --- |
| NL RIVM notifiable notifications | 2016–2025; 56 source categories; 518 NL observations and 20 Caribbean Netherlands observations | 30-minute checks for changes to an annual publication |
| MY MOH STD | 2017–2024; five source categories; 40 national observations | 30-minute checks for changes to an annual publication |

A 30-minute check does not make an annual dataset current weekly
surveillance. See the existing [NL](NL_RIVM_NOTIFIABLE_ANNUAL.md) and
[MY](MY_MOH_STD_ANNUAL.md) contracts.

## Netherlands: confirmed public weekly resources

### Virological laboratory weekly returns

- [Official explanation](https://www.rivm.nl/virologische-weekstaten).
- [Direct CSV](https://www.rivm.nl/sites/default/files/2019-01/RecenteVirUitslagen27w.csv),
  HTTP 200, report date 2026-11-15, latest source week 202645.
- The current file contains 49 source categories across a rolling 27-week
  window, 2026 W19–W45. Categories include influenza A/B/C, RSV, norovirus,
  rotavirus, hepatitis viruses, measles virus and other pathogens.
- The measure is participating laboratories' reported detections, not the
  annual table's approved nationwide notifiable-disease case counts. The file
  includes the number of reporting laboratories for each week, which varies.
- A connector should retain every original category and laboratory denominator,
  save each raw release, refresh the full rolling window for revisions and keep
  accumulated older database history after it leaves the public window. This
  one URL does not prove historical availability before that window.

### Respiratory surveillance

- [Official current figures](https://www.rivm.nl/luchtweginfecties/actueel)
  explicitly describe weekly publication on Wednesdays.
- [Public CKAN metadata API](https://data.rivm.nl/opendata/api/3/action/package_show?id=res-ari-surveillance),
  HTTP 200, metadata modified 2026-11-13. The reviewed package has 23 CSV
  resources and additional chart configuration resources.
- CSV downloads were verified for primary-care acute respiratory infection
  incidence, laboratory pathogen percentages and weekly SARI intensive-care
  admissions. Resources cover multiple respiratory pathogens, including
  influenza, RSV, rhinovirus/enterovirus, adenovirus, parainfluenza and hMPV.
- Resolve resource URLs from current package metadata; download filenames
  include release timestamps. Resources have distinct populations, units,
  denominators, seasonal/week encodings and revision windows. They must remain
  separate series. Do not infer dates from a bare week number without the
  associated season/calendar contract.
- The primary-care ARI page documents a methodology change from 2026-09-02;
  an adapter must preserve that definition change.

### Other current notification datasets

[Pertussis metadata](https://data.rivm.nl/opendata/api/3/action/package_show?id=kinkhoest)
and [measles metadata](https://data.rivm.nl/opendata/api/3/action/package_show?id=mazelen)
both returned HTTP 200 with CSV resources. Their latest metadata changes were
2026-10-26 and 2026-09-09 respectively. These have monthly/annual observations
and irregular publication updates; they are not verified weekly feeds.
The public pertussis article was older than its metadata resources, so article
modification time alone is not a reliable release detector.

The broad [notifiable-disease reports](https://www.rivm.nl/meldingsplicht-infectieziekten/rapportages)
page explicitly restricts detailed reports to authorized GGD/RIVM staff. A
public current API for every notifiable category has not been established.

## Malaysia: confirmed daily dengue, broader coverage still incomplete

[Official iDengue portal](https://idengue.mysa.gov.my/) returned HTTP 200 with
server-rendered national and state daily dengue case figures for 2026-11-15.
This daily dengue collection contract is separate from the STD
annual source. It is a public webpage contract; no stable documented API or
complete historical backfill contract has been verified here.

The page's cumulative death interval was explicitly 2026-01-01 through
2026-11-15. Its cumulative case heading instead referred to a start in 2023;
that discrepancy requires review. Do not treat that case total as verified
2026 year-to-date data or derive daily counts by differencing it. Prefer the
explicit daily national value and retain its source date and original labels.

The [MOH COVID dashboard](https://data.moh.gov.my/dashboard/covid-19) was
reachable, but displayed data through 2025-05-31 and an update date of
2025-06-01. It is not evidence of a currently maintained weekly feed.
The broader MOH press/report pages checked returned HTTP 403. No currently
working MOH-wide multi-disease weekly/monthly API was verified. Subsequent
[WHO investigation](MY_ADDITIONAL_SOURCES.md) verified Malaysian FluNet weekly
laboratory history from 1996 and GHO reported annual indicators from 1954.
HFMD, leptospirosis and complete current national notification coverage remain
gaps. A verified Sabah 2019 multi-disease report is state data, not national.
Further discovery verified Selangor's official 2025 annual report with multiple
communicable diseases. Its summary and disease chapters disagree for several
counts, so it remains a state-level candidate requiring reconciliation; see
the [additional-source investigation](MY_ADDITIONAL_SOURCES.md).

## Verified initial database imports

| Source scope | Observations | Coverage |
| --- | ---: | --- |
| `rivm_virological_weekly` | 1,323 | 49 categories × 27 source weeks |
| `rivm_respiratory_weekly` | 3,033 | 17 independently defined source resource series |
| `my_idengue_daily` | 16 | 2026-11-15; explicit national plus 15 source regions |
| `who_flunet_weekly` | 19,722 | 21 detection fields, 1996–2026, separate reporting origins |
| `who_gho_annual` | 553 | 15 reported indicators, 1954–2025; includes 60 explicit NULL observations |

NL respiratory collection preserves all 23 CSV resources and their chart
configurations; six CSVs without verified year/season context remain raw
evidence. NL facts retain original year/week or season/week in natural keys.
January 1 timestamps are explicitly marked storage anchors, not reconstructed
weekly dates. Public weekly plotting requires a verified calendar and an
explicit identity migration. Pertussis/measles datasets remain further candidates.

The observation counts above combine different units and populations only as
database row counts; they are not an epidemiological total. WHO historical
gaps remain source gaps and quality warnings, never synthesized zeroes.

All five connectors have completed raw capture, offline normalization, source
identity/quality checks, historical import where supported and an actual
scheduled run. Independent audits checked the full stored source facts against
captured artifacts. All five remain internal, with public release and mapping
notifications disabled. The merged verification suite passed 116 tests, and
the dashboard lint and production build passed.
