# WHO country-reported annual baseline for remaining regional countries

Verified against the actual WHO GHO JSON responses on 2026-11-16. This connector
adds the following country-specific annual reported-count baselines. These are
18 reviewed WHO indicators, not a claim that every national notifiable disease
or the country's recent weekly surveillance is covered. WHO is the distributor;
individual country reporting and revision dates can lag the current year.

| Code | Country | Observations | Numeric | Explicit unknown | Observed years |
| --- | --- | ---: | ---: | ---: | --- |
| ET | Ethiopia | 478 | 326 | 152 | 1970–2025 |
| KE | Kenya | 533 | 382 | 151 | 1971–2025 |
| UG | Uganda | 515 | 336 | 179 | 1971–2025 |
| GH | Ghana | 543 | 458 | 85 | 1970–2025 |
| TZ | Tanzania | 536 | 384 | 152 | 1974–2025 |
| PH | Philippines | 542 | 473 | 69 | 1961–2025 |
| VN | Viet Nam | 549 | 487 | 62 | 1950–2025 |
| LA | Lao PDR | 474 | 397 | 77 | 1953–2025 |
| MM | Myanmar | 544 | 476 | 68 | 1950–2025 |
| BN | Brunei Darussalam | 432 | 373 | 59 | 1955–2025 |
| TL | Timor-Leste | 282 | 256 | 26 | 2002–2025 |
| PY | Paraguay | 455 | 443 | 12 | 1974–2025 |
| EC | Ecuador | 471 | 445 | 26 | 1974–2025 |
| CR | Costa Rica | 452 | 437 | 15 | 1974–2025 |
| PA | Panama | 454 | 436 | 18 | 1974–2025 |

The live capture contains **7,260** source observations and **285** immutable
JSON artifacts, with 19 captures per country: indicator definitions and all 18
indicator endpoints, including successful empty collections. Year ranges are
extents, not continuity claims; different indicators have different ranges and
some endpoints have no country records. A successful empty indicator response
is recorded in the audit and does not create zero case counts.

## Contract and implementation

- Source scope: `who_gho_annual`; each country has its own source ID
  `SRC_<CC>_WHO_GHO`, geography `country:<CC>:national` and source label.
- Crawler: `src/data/crawlers/who_annual.py`; updater:
  `WHOAnnualUpdater(country_code)` in `src/data/processors/who_annual.py`.
- Durable production connector:
  `src/services/ingestion/countries/who_annual.py`.
- WHO [GHO landing page](https://www.who.int/data/gho) and
  [OData API](https://ghoapi.azureedge.net/api/).
- Each country query uses its reviewed ISO3 code, for example
  `WHS3_62?$filter=SpatialDim%20eq%20%27KEN%27`.
- WHO indicator definitions must match all 18 reviewed names and codes. The
  reviewed indicator set is shared with the existing Malaysia WHO adapter;
  Malaysia's implementation and source identity are unchanged.

The indicator set retains cholera cases/deaths; diphtheria; Japanese
encephalitis; pertussis; new leprosy cases; total tetanus; meningitis; polio;
H5N1; mumps; congenital rubella syndrome; neonatal tetanus; rubella; measles;
new/relapse TB; confirmed malaria cases; and reported malaria deaths. Several
indicators have no records for some countries. Total/neonatal tetanus and
case/death measures are non-additive independent source series.

Only country-level, unstratified, closed-calendar-year observations are
accepted. Estimate bounds, unknown stratifications, duplicate indicator/year
identities, foreign geography, future years, negative/fractional counts and
unrecognized text are rejected. Source zeroes, unknown values and explicit
suppression remain distinct. Historical WHO records with `NumericValue=null`
but an explicit integer in `Value` retain that published count. WHO source
record IDs, comments, time dimensions, raw hashes and indicator-definition
hashes are preserved on normalized rows.

## Revision and refresh

Routine publication checks capture the complete available history of each
reviewed indicator and upsert its existing country/indicator/year facts,
allowing corrections to older years. The monthly planner window does not
truncate this annual history. Explicit `start_year` requests may restrict a
manual organization; ordinary scheduled jobs omit that option. The 1900
validation lower bound retains the actually observed 1950/1953 histories and
is not a claim of available data since 1900.

All HTTP pages are persisted before normalization. Both pagination and offline
replay require a complete, acyclic page chain, endpoint-bound next links,
contiguous page indexes and the advertised count when provided. Organization
performs no source requests; its closed-year decision uses the original capture
time. A failed page or indicator prevents the incomplete collection from being
stored as a successful refresh. Valid stored facts are never withdrawn because
an incomplete refresh omitted a page. Full source withdrawals require separate
review; the current contract performs upserts, not authoritative deletion.

Country jobs use the shared scheduler, country concurrency guard, retry handling
and collect/organize/store runner. Source observation cadence remains annual
regardless of how often publication checks run. Public release remains disabled
pending source-terms review, disease mappings and verified generated snapshots.

## Reproducible capture and validation

```bash
# Actual live capture into local immutable artifacts and normalized CSVs;
# no database writes or scheduling changes.
venv/bin/python scripts/collect_remaining_who_annual.py

# A smaller group or one-country recapture.
venv/bin/python scripts/collect_remaining_who_annual.py --countries KE ET --concurrency 2

venv/bin/python -m pytest tests/unit/test_remaining_who_annual.py tests/unit/test_my_who_sources.py -q
```

Local capture receipts are
`reports/onboarding/remaining_who_annual_collection.json` and
`data/current/<cc>/who_gho_source_audit.json`. Per-country source CSVs are
`data/current/<cc>/who_gho_annual.csv`; original bytes and replay manifests are
under `data/raw/<cc>/who_gho_full_history/<capture-id>/`. Collection and offline
organization evidence does not by itself certify database import or public
release; those require their separate runner and database acceptance receipts.

The test fixtures in `tests/fixtures/ingestion/WHO/` contain actual captured
Kenyan and Ethiopian source records and complete reviewed indicator metadata.
They are bounded source excerpts for offline contract tests, not complete
country releases. Tests cover known case/death counts, zero/unknown/suppression,
legacy numeric text, foreign-country rejection, bounds/strata, duplicate and
future years, source-date replay, complete-indicator capture, raw provenance,
older revisions and updater isolation. Shared pagination tests are inherited
from the existing Malaysia WHO suite.
