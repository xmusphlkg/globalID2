# Provisional surveillance-data audit

Updated: 2026-08-13

## Display contract

- `provisional` / `preliminary`: the publisher says values are preliminary,
  incomplete, or still under case review. A trailing interval may be shaded.
- `closed_revisable` / `revised`: the reporting interval is closed but later
  corrections are possible. It is not shown as a provisional interval.
- `raw`: an internal validation state. It is never converted into a publisher
  provisional claim.
- If every point in a source series is provisional, the chart uses a
  source-wide status message instead of shading the whole plotting area.

## Country/source decisions

| Source | Published semantics | Ingestion policy | Audit result |
| --- | --- | --- | --- |
| Australia NNDSS | Dashboard updates daily; historical counts are retrospectively revisable | Open calendar month `provisional`; closed months `closed_revisable` | Missing distinction added |
| Brazil SINAN | Official FTP separates final and preliminary releases | Preserve file-level `final` / `preliminary` | Already explicit |
| Ontario PHO IDTO | Current monthly tab is explicitly preliminary | Current source rows `preliminary` | Already explicit |
| Switzerland FOPH IDD | API supplies point-level `dataComplete` | Explicit false/open incomplete month `provisional`; otherwise `closed_revisable` | API signal now propagated |
| China NHC/NDCPA monthly reports | Published as monthly reported counts; no source-wide provisional declaration found | Do not infer provisional; internal `raw` remains non-display metadata | Over-marking removed |
| Finland THL | Open current month can be requested; closed months remain revisable | Current month `provisional` only | Already explicit; boundary fixed |
| Hong Kong CHP | Official page says the most recent months are provisional and revised with updated information | Latest three published months `provisional`; earlier months `closed_revisable` | Missing marker added |
| Ireland HPSC weekly | Weekly/current and archive snapshots carry provisional semantics | Preserve source-wide provisional status | Already explicit |
| Iceland DOH current feeds | Current Power BI snapshots are explicitly provisional/revised | Preserve source-wide provisional status; historical workbooks remain separate | Already explicit |
| Japan JIHS IDWR | Feed is the IDWR preliminary/rapid table; prior totals change with delayed and discarded reports | Source-wide `provisional` | Missing marker added |
| Korea KDCA | Portal states each year's statistics are changeable provisional statistics | Source-wide `provisional` | Missing marker added |
| Norway FHI MSIS | Open current month is incomplete; closed months can be revised | Current month `provisional`; older months closed/revisable | Already explicit |
| New Zealand PHF Science EpiSurv | Monthly reports state that data are provisional and include cases still under investigation | Source-wide `provisional` | Missing marker added |
| Sweden SmiNet | Open month can be included as provisional; closed months are refreshed for revision | Current month `provisional`; closed months `closed_revisable` | Already explicit |
| Taiwan CDC NIDSS | Query results can change after later corrections | Open calendar month `provisional`; closed months `closed_revisable` | Missing distinction added |
| United States CDC NNDSS | Weekly data are provisional; annual tables are finalized | Weekly source-wide `provisional`; finalized annual sources remain final | Already explicit |
| Austria AGES / Germany RKI | Current connectors declare closed periods with authoritative revision semantics | `closed_revisable`, not provisional, until a source completeness flag is available | No unsupported marker added |

## Primary source evidence

- Australia NNDSS dashboard: https://nindss.health.gov.au/
- Hong Kong CHP monthly statistics: https://www.chp.gov.hk/en/static/24012.html
- Japan JIHS IDWR preliminary tables: https://id-info.jihs.go.jp/surveillance/idwr/provisional/sokuhou.html
- Korea KDCA infectious-disease statistics: https://dportal.kdca.go.kr/pot/is/summaryRgin.do
- New Zealand PHF Science monthly report example: https://www.phfscience.nz/media/fomj1vjz/202505_may25.pdf
- Ontario PHO IDTO: https://www.publichealthontario.ca/en/Data-and-Analysis/Infectious-Disease/Reportable-Disease-Trends-Annually
- Taiwan CDC NIDSS: https://nidss.cdc.gov.tw/Rods/Rods06?disease=1
- United States CDC NNDSS: https://www.cdc.gov/nndss/infectious-disease/
- Switzerland FOPH IDD data/API: https://www.idd.bag.admin.ch/en/portal-data

This audit records display semantics, not a claim that finalized surveillance
data are complete. Under-reporting, reporting delays, definition changes, and
later corrections remain separate epidemiologic limitations.
