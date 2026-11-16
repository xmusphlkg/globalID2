# SPF overseas annual notifications

Verified 2026-11-16. Official source: [Santé publique France ODISSÉ](https://odisse.santepubliquefrance.fr/).
The generic `spf_odisse_annual` connector supports Guadeloupe (`GP`, department
971/region 01), Martinique (`MQ`, 972/02), and French Guiana (`GF`, 973/03).
These explicit territorial rows are not added to France national observations.

## Source contract

Five reviewed datasets are fetched with complete JSON pagination:

| Dataset | Published measure and retained dimensions |
| --- | --- |
| `hepatite-a-donnees-declaration-obligatoire` | Annual hepatitis A notifications by residence department |
| `legionellose-donnees-declaration-obligatoire` | Annual legionellosis notifications by residence department and disease-onset year |
| `rougeole-donnees-declaration-obligatoire` | Annual measles notifications by age group, assigned by rash year and residence/notifier department |
| `tuberculose-donnees-declaration-obligatoire_dep` | Annual TB disease notifications; department of notification through 2018, department of residence from 2019 |
| `infection-invasive-a-meningocoque-notifications-region` | Annual invasive meningococcal notifications by age group and serogroup; residence/notifier region |

Age/serogroup totals and components remain independent non-additive source
categories. TB's 2019 geographic definition change gets a different definition
version. Rates and source population are retained alongside the count as source
fields; they never replace cases. The parser verifies the exact publisher,
dataset identifier/title, licence, cadence, required fields and territory labels.
Missing source years are not synthesized as zero. Explicit zeroes and null or
suppressed values are distinguished. Current or future calendar years fail.

The initial verified captures contain:

| Territory | Source records / count observations | Earliest–latest available year |
| --- | ---: | --- |
| GP | 1,194 | 1998–2025 |
| MQ | 1,197 | 1998–2025 |
| GF | 1,198 | 1998–2025 |

Each total includes separate age and serogroup records. It is not a case sum.
Coverage ranges differ by dataset and contain gaps, especially hepatitis A.
This source contract covers these five datasets and does not claim exhaustive
territory surveillance. A leptospirosis-titled catalog entry with a TBE
metadata description was rejected rather than silently integrated.

## Capture, replay and refresh

`src/services/ingestion/countries/spf_overseas.py:collect` takes `country_code`.
Each run captures original metadata, every counted page in stable dimension
order, and final metadata. Metadata or total-count changes during pagination,
truncated pages and errors fail the run. Organization requires exact page
continuity and integrity-checked artifact bytes and makes no network calls.

`SPFOverseasUpdater(country_code)` exposes ontology ID
`SRC_<GP|MQ|GF>_SPF_ODISSE`. Source-native facts use the existing shared holding
series path; no legacy disease-record projection is implied. The 2026-11-16
admission review permits only the reviewed SPF source system in each territory;
legacy publication remains disabled. All five datasets state Licence ouverte
2.0, with visible attribution to Santé publique France, original dataset titles,
years and extraction date. The source description explicitly discloses the TB
notification-location/residence-location break; the representative curve may
select only the recent residence series while retaining older facts in source
details. Snapshot and publication receipts are validated separately.

Refresh rereads all available source history, even for a small scheduler window,
so revised old counts can upsert existing facts. An absent future source cell
does not authorize deletion. Polling is separate from annual observation
cadence; normal control-panel retries and country concurrency apply.

Local verification without database writes:

```bash
venv/bin/python scripts/collect_spf_overseas_history.py
venv/bin/python scripts/collect_spf_overseas_history.py --replay
venv/bin/python -m pytest tests/unit/test_spf_overseas.py -q
```

The local capture preserves all metadata and pages in
`data/raw/<territory>/spf_odisse_history/artifacts_manifest.json`, emits
`data/current/<territory>/spf_odisse_annual.csv` and `spf_odisse_source_audit.json`.
These local receipts are not database-import receipts. Staged production
collection uses the existing `ArtifactWriter` and the normal ingestion store.
