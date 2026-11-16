# US CDC refresh ranges

Both US jobs check for updates every **30 minutes**. Source observations keep
their native cadence: NNDSS is weekly and NHSS HIV is annual.

Normal NNDSS checks query the persisted NNDSS US-resident checkpoint and fetch
from eight weeks before it, including the checkpoint week. NHSS, TOTAL and
other geographies cannot advance that checkpoint. National collections retain
all admitted state and territory rows in the same date window.

Scheduled tasks ignore a legacy `fill_missing=true` input unless explicitly
forced. This also bounds already queued tasks after a configuration edit.
Manual fill-missing and forced replays remain available; an empty database
still bootstraps source history.

## Historical coverage

2014–2021 archive tables produce state/territory facts, not US-resident
national facts. Missing archive years are determined by captured archive
artifacts joined to a **completed store stage in the same task attempt**, with
`process=true`. Existing successful runs serve as receipts without a migration.
A national run covers all admitted subdivisions; a subdivision run only proves
coverage for itself. Failed attempts and collection-only runs never count.
Explicitly planned archive years must contain usable rows before storing.

These receipts prove a successful import, not every possible source cell or
the continued presence of rows after manual deletion. Use a forced replay to
rebuild deleted history or reprocess an already completed archive.

## Archive download reuse

Archive discovery is refreshed upstream whenever historical collection is
requested. Table pages are cached in `data/cache/us/nndss_archive/`, keyed by
resource URL, revision metadata, column fields, page offset and page size.
Each cache hit verifies its SHA-256 and receipt age; pages are fetched again
after 30 days. Missing revision metadata, corruption, source revision changes
and force mode require a fresh download.

File locks share downloads between tasks. Complete pages survive a failure on
a later page, so retries reuse them. Every reuse still records an independent
source artifact for the task. Recent current CSV pages are fetched fresh.

## Apply configuration

```sh
venv/bin/python scripts/configure_us_updates.py
venv/bin/python scripts/configure_us_updates.py --apply \
  --receipt output/us-incremental/configuration.json
```

The script updates only `us-daily` and `us-nhss-hiv-weekly`, retains existing
job IDs and operator pauses, sets both intervals to 30 minutes and disables
routine historical filling. Persisted schedule state is refreshed as well.
`scripts/us_configure_hiv_automation.py` applies the same NHSS policy.

After updating code, let the ingestion worker restart gracefully before its
next task. A task already executing keeps its existing collected scope.
