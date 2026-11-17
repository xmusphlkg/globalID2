# Bangladesh, Bhutan, Maldives and Afghanistan

Integration date: 2026-11-17. Each collector captures official directories and
original reports, verifies their source links and hashes during offline replay,
and writes source-native observations through the existing staged runner.
Source categories remain in the mapping registry's holding series until reviewed.

| Country | Independent source | Contract and limits |
| --- | --- | --- |
| BD | `bd_dghs_dengue_daily` / `SRC_BD_DGHS_DENGUE` | Hospital admissions and deaths, daily and year-to-date separately; national, Division and city scopes retain distinct identities. [Details](BD_DGHS_DENGUE.md). |
| BT | `bt_rcdc_publications` / `SRC_BT_RCDC_PUBLICATIONS` | Respiratory, monthly, quarterly, annual and event publications; explicit single-week influenza laboratory detections. Event snapshots remain metadata. [Details](BT_RCDC_INTEGRATION.md). |
| MV | `mv_hpa_communicable` / `SRC_MV_HPA_COMMUNICABLE` | Official HPA monthly reports in the Ministry archive; printed current-year case labels, with historical editions and conflicting values audited. [Details](MV_HPA_INTEGRATION.md). |
| AF | `af_who_outbreaks_weekly` / `SRC_AF_WHO_OUTBREAKS` | Verified WHO Afghanistan reporting-site weekly matrices; 2025 window plus 333 exact historical observations from 2023–2024; independent from MoPH NDSR. [Details](AF_WHO_OUTBREAKS_INTEGRATION.md). |
| AF | `af_ndsr_weekly` / `SRC_AF_MOPH_NDSR` | MoPH connector and captured access diagnostics; actual numerical report format remains unverified because originals could not be retrieved. [Details](AF_NDSR_INTEGRATION.md). |

Afghanistan defaults to the verified WHO feed. Source scopes, labels, country
metadata, updater dispatch and refresh-channel definitions are registered
centrally. The weekly sources being developed for IN/PK/NP/LK retain their own
contracts in `south_asia_weekly_sources.py`.

## Collection and replay

Capture originals, export CSV and write a local receipt without database writes:

```bash
venv/bin/python scripts/collect_south_asia_sources.py --countries BD BT MV AF --max-reports 100
```

The result prints a manifest under `data/raw/<country>/.../manifest.json` and
stores its observation CSV and audit under `data/current/<country>/.../`.
Inspect or import the same immutable originals without additional source requests:

```bash
venv/bin/python scripts/imports/import_surveillance_archive.py --country BD --manifest data/raw/bd/<capture>/manifest.json
venv/bin/python scripts/imports/import_surveillance_archive.py --country BD --manifest data/raw/bd/<capture>/manifest.json --apply
```

An explicit AF scope can select `af_ndsr_weekly`; the default archive source is
`af_who_outbreaks_weekly`. `max_reports` remains a capture bound, and start years
are search windows rather than completeness claims. Missing periods are never
synthesized. A numerical processing task fails if the source yields no verified
observations, after saving its organize-stage audit; collect-only diagnostics
can still retain a document-only or inaccessible-source result.

## Coverage and publication

The checked-in [four-country evidence receipt](evidence/south-asia-four-20261117.json)
records capture counts, database import tasks, date ranges and remaining gaps.
Detailed receipts and raw manifests remain in local data directories.

IEDCR ILI/SARI has no verified numerical feed in this integration. The supplied
legacy DGHS dashboard URL returned 404; current DGHS official PDFs are used.
No public Maldives dengue weekly feed or SIDAS API was established. Available
Maldives monthly publications and WHO Afghanistan weekly archives lag the
current date; their latest verified reporting dates are retained explicitly.

No new public snapshot has been generated. Public release is disabled in source
and country contracts; these observations require source-category mapping review
before admission to public comparison curves. Refresh channels are saved with
`enabled_by_default=false`.
