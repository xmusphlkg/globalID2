# Switzerland FOPH IDD Data Plan

## Source Analysis

- Source: Switzerland FOPH/BAG Infectious Diseases Dashboard (IDD), `https://www.idd.bag.admin.ch/en/portal-data`.
- API base: `https://www.idd.bag.admin.ch/api/v1`.
- Version endpoint: `/data/version`.
- Discovery endpoint: `/data/sets`.
- Data endpoint pattern: `POST /data/{disease}/cases/value/{period}`.
- Details endpoint pattern: `GET /data/{disease}/cases/value/{period}/details`.
- Update cadence: weekly on Wednesdays, per the IDD portal data page.

## Normalization Contract

- Canonical country code: `CH`.
- Canonical source scope: `foph_idd`.
- Stored source label: `Switzerland FOPH IDD Mandatory Reporting System`.
- Preferred grain per disease:
  - `month` when the IDD exposes monthly case totals.
  - `iso_week` for Covid-19 and influenza.
  - `year` for diseases that only expose annual totals.
- Geography:
  - Prefer `country=CH` where the API exposes it.
  - Use `CHFL` for monthly series where the dashboard only exposes the combined Switzerland + Liechtenstein aggregate.
  - Persist the selected geography in `disease_records.metadata.geographies`.

## Historical Import

Run:

```bash
venv/bin/python3 scripts/import_ch_history.py --start-year 2013
```

The script upserts:

- country metadata and country scope for `CH`
- standard disease rows from `configs/standard_diseases.csv`
- CH mappings from `configs/mapping/ch.csv`
- normalized IDD historical rows into `disease_records`

It is additive and does not delete other countries' data.

## Dynamic Update Mechanism

Use the existing crawl path:

```bash
venv/bin/python3 main.py crawl --country CH --source foph_idd --process --save-raw
```

Recommended automation:

- Schedule weekly after the IDD Wednesday release, for example Wednesday 18:00 Europe/Zurich.
- Normal update uses recent configured periods:
  - recent 6 monthly periods
  - recent 12 ISO weeks
  - recent 2 annual periods
- `--fill-missing` adds database-missing month buckets to the next run.
- `--force --start-year 2013` refreshes all available historical IDD rows.

Because IDD can revise published values, every fetched row is upserted by
`(time, disease_id, country_id)`.
